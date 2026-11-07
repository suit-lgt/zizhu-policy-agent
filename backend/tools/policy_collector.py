"""资助政策官方文件采集器（R-001 政策入库的前置步骤）。

原则（对应 AGENTS.md I-6/I-7 与 docs/data-contract.md）：
- 只采公开栏目页，低频、单线程、按域名节流；不绕登录、不绕人机验证。
- 采到的是草稿：文号、发布机关、生效日期一律标 pending_verify，人工核对后才可发布。
- 原文快照与正文一并留存，按内容哈希去重，重复采集不产生新记录。

用法：
  python -X utf8 backend/tools/policy_collector.py discover --list https://www.xszz.edu.cn/n39/n57/index.html
  python -X utf8 backend/tools/policy_collector.py crawl --preset 国家 --limit 2        # 批量走国家 6 个兄弟栏目（增量）
  python -X utf8 backend/tools/policy_collector.py crawl --list <栏目页1> <栏目页2> --limit 3
  python -X utf8 backend/tools/policy_collector.py split                                # 条款切块入库
  python -X utf8 backend/tools/policy_collector.py verify-clauses                       # 校验可回点原文
  python -X utf8 backend/tools/policy_collector.py gui

依赖：仅标准库。存储：SQLite（data/staging/policies.db）+ 原文快照（data/staging/raw/）。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "staging"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "policies.db"

USER_AGENT = "ZiZhuPolicyCollector/0.1 (educational research; low-rate; contact via project owner)"
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 白名单域名：只允许这些站点，防止误配到非官方来源
ALLOWED_HOSTS = {
    "www.xszz.edu.cn",
    "xszz.edu.cn",
    "www.moe.gov.cn",
    "moe.gov.cn",
    "edu.gd.gov.cn",
    "czt.gd.gov.cn",
    "www.gd.gov.cn",
    "gd.gov.cn",
    "szeb.sz.gov.cn",
}

# 栏目预设：一次把同一站点的兄弟栏目全走一遍（标题用于日志，URL 用官方栏目页）
COLUMN_PRESETS = {
    "国家": [
        ("综合政策", "https://www.xszz.edu.cn/n39/n53/index.html", "国家"),
        ("高校学生资助", "https://www.xszz.edu.cn/n39/n54/index.html", "国家"),
        ("中等职业教育", "https://www.xszz.edu.cn/n39/n55/index.html", "国家"),
        ("普通高中", "https://www.xszz.edu.cn/n39/n56/index.html", "国家"),
        ("义务教育", "https://www.xszz.edu.cn/n39/n57/index.html", "国家"),
        ("学前教育", "https://www.xszz.edu.cn/n39/n58/index.html", "国家"),
    ],
    "广东省级": [
        ("高校信息公开-高校资助", "https://edu.gd.gov.cn/zwgknew/gxxxgkzl/xxgkgz/", "省级"),
    ],
}

DOC_NO_PATTERNS = (
    # 首选：括号包裹的完整文号，如（财教〔2025〕69号）
    re.compile(r"[（(]([^（）()]{2,24}〔\d{4}〕(?:\d{1,4}|第\d{1,3})号)[）)]"),
    # 次选：以标点或行首为边界的独立文号 token
    re.compile(r"(?:^|》|《|、|，|,|：|:|\s)([^（）()《》、，,：:\s]{2,10}〔\d{4}〕(?:\d{1,4}|第\d{1,3})号)"),
    # 兜底：只取〔年份〕序号号，前缀留空等待人工补
    re.compile(r"(〔\d{4}〕(?:\d{1,4}|第\d{1,3})号)"),
)
DATE_RE = re.compile(r"(\d{4})[-年.](\d{1,2})[-月.](\d{1,2})")
EFFECTIVE_RE = re.compile(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起(?:施行|实施|执行)")
SIGN_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
BANNED_MARKERS = ("请输入验证码", "人机验证", "访问过于频繁", "请开启JavaScript", "滑动验证")


class Blocked(Exception):
    """站点要求验证或拒绝访问，按规则停止，不做绕过。"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def sign_date_of(text: str) -> str:
    """落款日期：正文末尾出现的中文日期。与栏目页列表日期不是一回事，仅作核对参照。"""
    tail = text[-160:]
    dates = SIGN_RE.findall(tail)
    if not dates:
        return ""
    y, m, d = dates[-1]
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def days_between(a: str, b: str) -> int | None:
    from datetime import date

    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except ValueError:
        return None


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_staging (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            host TEXT NOT NULL,
            layer TEXT,
            title TEXT,
            doc_no TEXT,
            issuer TEXT,
            publish_date TEXT,
            effective_from TEXT,
            effective_to TEXT,
            content_hash TEXT,
            body_text TEXT,
            raw_path TEXT,
            fetched_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending_verify',
            note TEXT
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_hash ON policy_staging(content_hash)")
    # 条款表：每条记录带字符偏移，可回到原文位置；随政策版本锚定（version = 正文哈希前 12 位）
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_clause (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            clause_no TEXT,
            chapter TEXT,
            clause_type TEXT NOT NULL,
            text TEXT NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            char_count INTEGER NOT NULL,
            version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_verify',
            publishable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            UNIQUE(doc_id, version, seq)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clause_doc ON policy_clause(doc_id, seq)")
    # 采集运行记录：多栏目批量的增量对比要有账可查
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_run (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            list_url TEXT,
            layer TEXT,
            candidates INTEGER,
            inserted INTEGER,
            updated INTEGER,
            skipped INTEGER,
            failed INTEGER,
            note TEXT
        )
        """
    )
    conn.commit()
    return conn


def check_host(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"域名不在白名单：{host}。官方来源外的一律不采。")


def http_get(url: str, timeout: int = 15, browser_ua: bool = False) -> tuple[str, bytes]:
    check_host(url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA_BROWSER if browser_ua else USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络不可达：{url}（{exc.reason}）") from exc
    if code != 200:
        raise RuntimeError(f"HTTP {code}：{url}")
    text = decode_html(raw, ctype)
    for marker in BANNED_MARKERS:
        if marker in text[:5000]:
            raise Blocked(f"站点出现验证或拦截提示（{marker}），已停止，不绕过：{url}")
    return text, raw


def decode_html(raw: bytes, ctype: str) -> str:
    charset = ""
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    if m:
        charset = m.group(1)
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:2000], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in [charset, "utf-8", "gb18030"]:
        if not enc:
            continue
        try:
            return raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(p|div|tr|li)>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", fragment)
    text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def robots_disallows(url: str, timeout: int = 10) -> tuple[list[str], str]:
    """返回 (disallow 规则, 说明)。无 robots.txt 视为未声明限制，但仍须低频。"""
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # 404 / 超时都按"未声明"处理，不阻断
        return [], f"无可用 robots.txt（{type(exc).__name__}），按未声明限制处理，间隔≥3s"
    rules: list[str] = []
    applies = False
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("user-agent:"):
            applies = line.split(":", 1)[1].strip() in {"*", "", "ZiZhuPolicyCollector"}
        elif applies and line.lower().startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                rules.append(path)
    return rules, f"robots.txt 声明 {len(rules)} 条 Disallow"


def robots_allowed(path: str, rules: list[str]) -> bool:
    return not any(path.startswith(rule) for rule in rules if rule != "/")


def _clean_title(fragment: str) -> str:
    """标题优先取 h5 的 title 属性（完整），其次标题标签文本，最后才是链接文本。"""
    m = re.search(r'<(?:h\d|strong|b)[^>]*title=["\']([^"\']+)["\']', fragment, re.I)
    if m and len(m.group(1).strip()) >= 6:
        return html.unescape(m.group(1)).strip()
    m = re.search(r"<(?:h\d|strong|b)[^>]*>(.*?)</(?:h\d|strong|b)>", fragment, re.S | re.I)
    if m:
        text = strip_tags(m.group(1))
        if len(text) >= 6:
            return text
    text = strip_tags(fragment.split("<p", 1)[0] or fragment)
    return re.sub(r"^[#\s]+", "", text).strip()


def _date_in(block: str) -> str:
    m = re.search(r"<b>\s*(\d{4})\s*</b>\s*(\d{1,2})-(\d{1,2})", block, re.I)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    dates = DATE_RE.findall(block)
    if dates:
        y, mo, d = dates[-1]
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return ""


def parse_list(page_html: str, base_url: str, include: str = "") -> list[dict]:
    """从栏目页抽取条目：标题、链接、日期。只保留同域详情页。

    主体按 <li> 块解析（全国学生资助管理中心与广东省教育厅的列表都适用）；
    无 <li> 结构时退化为逐链接解析。
    """
    base_host = urllib.parse.urlparse(base_url).netloc
    items: list[dict] = []
    seen: set[str] = set()

    def add(href: str, fragment: str, block: str) -> None:
        url = urllib.parse.urljoin(base_url, href.strip())
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != base_host:
            return
        if not re.search(r"/c\d+/content|/content/post_\d+|(?:content|post)_\d+\.(?:html|shtml)$", parsed.path, re.I):
            return
        if url in seen:
            return
        title = _title_clean(_clean_title(fragment))
        if len(title) < 8:
            return
        if include and include not in title:
            return
        publish = _date_in(block) or _date_in(fragment)
        seen.add(url)
        items.append({"url": url, "title": title, "publish_date": publish})

    for block in re.findall(r"<li[^>]*>(.*?)</li>", page_html, re.S | re.I):
        m = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\']', block, re.I)
        if m:
            add(m.group(1), block, block)

    if not items:
        for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_html, re.S | re.I):
            block = page_html[max(0, m.start() - 400) : m.end() + 400]
            add(m.group(1), m.group(2), block)
    return items


def _title_clean(title: str) -> str:
    title = re.sub(r"\s+\d{4}-\d{1,2}-\d{1,2}\s*$", "", title)  # 列表标题尾部日期
    title = re.sub(r"\s*\[查看更多\]\s*$", "", title)
    return title.strip()


def parse_detail(page_html: str) -> dict:
    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.S | re.I)
    if m:
        title = strip_tags(m.group(1))
    if not title:
        m = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
        title = strip_tags(m.group(1)) if m else ""
    title = re.sub(r"\s*[-—_]\s*(?:全国学生资助管理中心|广东省教育厅|.*政府门户网站|.*教育厅网站)\s*$", "", title)
    title = re.sub(r"^\s*[-—_]\s*", "", title)
    title = title.strip()

    body = page_html
    container = re.search(
        r'<(?:div|article|section)[^>]*(?:id|class)=["\']?[^"\'>]*'
        r'(?:article|txtSize|content|zoom|TRS_Editor|detail|newsinfo|view)[^>]*>',
        page_html,
        re.I,
    )
    if container:
        body = page_html[container.end():]
    for tail_marker in ("版权所有", "主办单位", "关注 微信公众号", "关注 视频号", "联系我们", "关于我们"):
        idx = body.find(tail_marker)
        if idx > 400:
            body = body[:idx]
    text = strip_tags(body)
    for head_marker in ("访问量:", "【小】"):
        idx = text.find(head_marker)
        if 0 <= idx < 600:
            text = text[idx + len(head_marker):]
    # 页脚专属标记：出现即视为正文结束（不受位置限制，正文不会以这些词开头）
    for tail_marker in ("版权所有", "公网安备", "网站标识码", "备案/许可证号", "全国学生资助管理中心 -"):
        idx = text.find(tail_marker)
        if idx > 80:
            text = text[:idx]
    # 通用页脚词：仅在正文足够长时生效，避免误伤正文
    for tail_marker in ("主办单位", "关注 微信公众号", "关注 视频号", "使用帮助", "联系我们", "关于我们"):
        idx = text.find(tail_marker)
        if idx > 600:
            text = text[:idx]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    while lines and len(lines[-1]) <= 2:
        lines.pop()
    text = "\n".join(lines)
    # 文号判定：先看正文最前面的独立文号（多数文件的本文文号在页首），再看标题；
    # 正文深处的文号多为"引用他件"，不得当作本文文号（I-7）。
    doc_no = ""
    head_zone = text[:60]
    for pattern in DOC_NO_PATTERNS[1:]:
        m = pattern.search(head_zone)
        if m:
            doc_no = m.group(1).strip()
            break
    if not doc_no:
        for pattern in DOC_NO_PATTERNS:
            m = pattern.search(title)
            if m:
                doc_no = m.group(1).strip()
                break
    publish = ""
    m = re.search(r"发布时间[:：]\s*(\d{4})-(\d{1,2})-(\d{1,2})", text[:2000])
    if m:
        publish = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    issuer = ""
    cut = re.search(r"《", title)
    if cut and cut.start() > 0:
        issuer = title[: cut.start()].strip(" ，,、")
    eff = ""
    m = EFFECTIVE_RE.search(text)
    if m:
        eff = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return {
        "title": title,
        "doc_no": doc_no,
        "issuer": issuer,
        "publish_date": publish,
        "effective_from": eff,
        "sign_date": sign_date_of(text),
        "body_text": text,
        "content_hash": hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest(),
    }


def save_raw(url: str, raw: bytes) -> str:
    host = urllib.parse.urlparse(url).netloc
    stem = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    folder = RAW_DIR / host
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.html"
    path.write_bytes(raw)
    return str(path.relative_to(ROOT))


def crawl(
    list_url: str,
    limit: int,
    layer: str,
    delay: float,
    browser_ua: bool,
    log,
    include: str = "",
    force: bool = False,
) -> dict:
    """采一个栏目页。默认增量：已采过的详情页不再请求，只补新条目。

    增量判定以详情页 URL 为键；`force=True` 才会重取旧条目，内容哈希变化则更新记录。
    每次运行都写一条 crawl_run，便于回答"这批新条目是哪次、哪个栏目来的"。
    """
    check_host(list_url)
    ensure_dirs()
    started = now_utc()
    rules, robots_note = robots_disallows(list_url)
    log(f"robots：{robots_note}")
    parsed = urllib.parse.urlparse(list_url)
    if not robots_allowed(parsed.path, rules):
        raise Blocked(f"robots 禁止访问 {parsed.path}，按规则停止")

    log(f"读取栏目页：{list_url}")
    page, _ = http_get(list_url, browser_ua=browser_ua)
    items = parse_list(page, list_url, include)
    log(f"栏目页识别到 {len(items)} 条候选，按 --limit={limit} 取前若干条")

    conn = connect()
    known = dict(conn.execute("SELECT url, content_hash FROM policy_staging"))
    new = updated = skipped = dup = failed = 0
    detail_budget = limit
    if not items:
        conn.execute(
            "INSERT INTO crawl_run (started_at,list_url,layer,candidates,inserted,updated,skipped,failed,note)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (started, list_url, layer, 0, 0, 0, 0, 0, "栏目页未识别到条目，可能结构变化"),
        )
        conn.commit()
        conn.close()
        return {"new": 0, "updated": 0, "skipped": 0, "duplicate": 0, "failed": 0, "note": "栏目页未识别到条目"}

    attempts = 0
    attempt_cap = max(limit * 3, 6)
    for item in items:
        if detail_budget <= 0 or attempts >= attempt_cap:
            break
        if item["url"] in known and not force:
            skipped += 1
            log(f"[跳过] 已采集过：{item['url']}")
            continue
        if not robots_allowed(urllib.parse.urlparse(item["url"]).path, rules):
            log(f"跳过（robots 限制）：{item['url']}")
            continue
        time.sleep(delay)
        try:
            page_html, raw = http_get(item["url"], browser_ua=browser_ua)
        except Blocked as exc:
            log(f"中止：{exc}")
            break
        except Exception as exc:
            failed += 1
            attempts += 1
            log(f"失败：{item['url']}（{exc}）")
            continue
        detail = parse_detail(page_html)
        body = detail["body_text"]
        if len(body) < 200:
            # 附件下载页、样表页不是政策正文：跳过并继续，不占新增额度
            skipped += 1
            attempts += 1
            log(f"跳过（非政策正文，{len(body)} 字）：{item['url']}")
            continue
        raw_path = save_raw(item["url"], raw)
        note = []
        if not detail["doc_no"]:
            note.append("未见文号")
        if not detail["effective_from"]:
            note.append("未见施行日期")
        gap = days_between(detail["publish_date"], detail["sign_date"]) if detail.get("sign_date") else None
        if gap is not None and gap > 200:
            note.append(f"列表日期与落款相差 {gap} 天（落款 {detail['sign_date']}），发文日期需核对")
        values = (
            layer,
            detail["title"] or item["title"],
            detail["doc_no"] or "pending_verify",
            detail["issuer"] or "pending_verify",
            detail["publish_date"] or item["publish_date"] or "pending_verify",
            detail["effective_from"] or "pending_verify",
            "pending_verify",
            detail["content_hash"],
            body,
            raw_path,
            now_utc(),
            "pending_verify",
            "；".join(note),
        )
        if item["url"] in known:
            if known[item["url"]] == detail["content_hash"]:
                skipped += 1
                log(f"[未变] 内容哈希一致：{item['url']}")
                continue
            conn.execute(
                """UPDATE policy_staging SET layer=?,title=?,doc_no=?,issuer=?,publish_date=?,effective_from=?,
                   effective_to=?,content_hash=?,body_text=?,raw_path=?,fetched_at=?,status=?,note=?
                   WHERE url=?""",
                values + (item["url"],),
            )
            conn.commit()
            updated += 1
            log(f"[更新] 内容有变化：{detail['title'][:40]}")
            detail_budget -= 1
            continue
        try:
            conn.execute(
                """
                INSERT INTO policy_staging
                (url, host, layer, title, doc_no, issuer, publish_date, effective_from, effective_to,
                 content_hash, body_text, raw_path, fetched_at, status, note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (item["url"], urllib.parse.urlparse(item["url"]).netloc) + values,
            )
            conn.commit()
            new += 1
            detail_budget -= 1
            known[item["url"]] = detail["content_hash"]
            log(f"[新增] {detail['title'][:40]} | 文号 {detail['doc_no'] or '未见'}")
        except sqlite3.IntegrityError:
            dup += 1
            attempts += 1
            log(f"[重复] 内容哈希已存在（同一文件在其他栏目重复挂载）：{item['url']}")
            continue
    conn.execute(
        "INSERT INTO crawl_run (started_at,list_url,layer,candidates,inserted,updated,skipped,failed,note)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (started, list_url, layer, len(items), new, updated, skipped, failed, robots_note),
    )
    conn.commit()
    conn.close()
    return {
        "new": new,
        "updated": updated,
        "skipped": skipped,
        "duplicate": dup,
        "failed": failed,
        "note": robots_note,
    }


def crawl_many(
    columns: list[tuple[str, str, str]],
    limit: int,
    delay: float,
    browser_ua: bool,
    log,
    include: str = "",
    force: bool = False,
) -> dict:
    """批量走多个栏目：每栏 limit 是"新增上限"，已采过的不占额度。"""
    totals = {"new": 0, "updated": 0, "skipped": 0, "duplicate": 0, "failed": 0}
    per_column = []
    for title, url, layer in columns:
        log(f"==== 栏目：{title}（{url}）")
        try:
            result = crawl(url, limit, layer, delay, browser_ua, log, include, force)
        except Blocked as exc:
            log(f"栏目中止：{exc}")
            per_column.append({"column": title, "url": url, "error": str(exc)})
            break
        except Exception as exc:
            log(f"栏目失败：{exc}")
            per_column.append({"column": title, "url": url, "error": str(exc)})
            continue
        per_column.append({"column": title, "url": url, **{k: result[k] for k in totals}})
        for k in totals:
            totals[k] += result.get(k, 0)
    return {"totals": totals, "columns": per_column}


def discover(list_url: str, include: str = "") -> dict:
    check_host(list_url)
    page, _ = http_get(list_url)
    columns = []
    for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page, re.S | re.I):
        url = urllib.parse.urljoin(list_url, m.group(1))
        title = strip_tags(m.group(2))
        if re.search(r"/n\d+/n\d+/index\.html", url) and (
            (2 <= len(title) <= 12 and title.endswith("资助")) or title in {"综合政策", "政策文件"}
        ):
            columns.append({"title": title, "url": url})
    items = parse_list(page, list_url, include)
    return {"list_url": list_url, "columns": columns, "item_count": len(items), "sample": items[:5]}


def gui(default_list: str) -> int:
    """固定窗口：填栏目页、点"开始采集"，进度实时显示。"""
    import threading
    import queue
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    q: queue.Queue = queue.Queue()

    root = tk.Tk()
    root.title("资助政策官方文件采集")
    root.geometry("760x560")

    tk.Label(root, text="栏目页 URL（官方站，仅白名单域名）").pack(anchor="w", padx=12, pady=(12, 2))
    entry = tk.Entry(root, width=100)
    entry.insert(0, default_list)
    entry.pack(anchor="w", padx=12)

    bar = tk.Frame(root)
    bar.pack(anchor="w", padx=12, pady=8)
    tk.Label(bar, text="最多条数").pack(side="left")
    limit_var = tk.StringVar(value="5")
    tk.Spinbox(bar, from_=1, to=30, textvariable=limit_var, width=5).pack(side="left", padx=(4, 16))
    tk.Label(bar, text="间隔秒").pack(side="left")
    delay_var = tk.StringVar(value="3")
    tk.Spinbox(bar, from_=1, to=10, increment=0.5, textvariable=delay_var, width=5).pack(side="left", padx=(4, 16))
    tk.Label(bar, text="层级").pack(side="left")
    layer_var = tk.StringVar(value="国家")
    ttk.Combobox(bar, textvariable=layer_var, values=["国家", "省级", "市级", "校级"], width=8).pack(side="left", padx=4)
    tk.Label(bar, text="标题关键词").pack(side="left")
    kw_var = tk.StringVar(value="")
    tk.Entry(bar, textvariable=kw_var, width=10).pack(side="left", padx=(4, 0))

    log_box = tk.Text(root, height=22, wrap="word")
    log_box.pack(fill="both", expand=True, padx=12, pady=(4, 8))

    def log(msg: str) -> None:
        q.put(msg)

    def worker() -> None:
        try:
            result = crawl(
                entry.get().strip(),
                int(limit_var.get()),
                layer_var.get(),
                float(delay_var.get()),
                False,
                log,
                kw_var.get().strip(),
            )
            q.put(f"完成：新增 {result['new']}，重复 {result['duplicate']}，失败 {result['failed']}")
        except Exception as exc:
            q.put(f"停止：{exc}")

    def start() -> None:
        log_box.delete("1.0", "end")
        threading.Thread(target=worker, daemon=True).start()

    def pump() -> None:
        while not q.empty():
            log_box.insert("end", q.get() + "\n")
            log_box.see("end")
        root.after(300, pump)

    btns = tk.Frame(root)
    btns.pack(anchor="w", padx=12, pady=(0, 12))
    tk.Button(btns, text="开始采集", width=12, command=start).pack(side="left")
    tk.Button(
        btns,
        text="打开输出目录",
        width=14,
        command=lambda: os.startfile(DATA_DIR) if DATA_DIR.exists() else messagebox.showinfo("提示", "尚无输出，先采集一次"),
    ).pack(side="left", padx=8)
    tk.Button(btns, text="查看采集结果", width=14, command=lambda: messagebox.showinfo("采集库", str(DB_PATH))).pack(
        side="left"
    )

    pump()
    root.mainloop()
    return 0


ARTICLE_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]{1,6}条")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]{1,6}章")
APPENDIX_RE = re.compile(r"^附(?:件|表|录)\s*[:：]?")


def _line_offsets(body: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    pos = 0
    for line in body.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def split_document(body: str) -> list[dict]:
    """把正文切成条款：优先按"第X章/第X条"结构，无条结构时按自然段。

    每条记录带 char_start/char_end，`body[start:end]` 必须等于该条正文——
    这是"引用可回点原文位置"的硬约束（I-3），入库前会逐条校验。
    """
    lines = _line_offsets(body)
    article_lines = [i for i, (_, ln) in enumerate(lines) if ARTICLE_RE.match(ln.strip())]
    clauses: list[dict] = []

    def push(start: int, end: int, ctype: str, clause_no: str, chapter: str) -> None:
        text = body[start:end].strip()
        if not text:
            return
        clauses.append(
            {
                "seq": len(clauses) + 1,
                "clause_no": clause_no,
                "chapter": chapter,
                "clause_type": ctype,
                "text": text,
                "char_start": start,
                "char_end": end,
            }
        )

    if len(article_lines) >= 3:
        breaks = [i for i, (_, ln) in enumerate(lines) if CHAPTER_RE.match(ln.strip()) or ARTICLE_RE.match(ln.strip())]
        first = breaks[0]
        if first > 0:
            push(lines[0][0], lines[first][0], "前言", "", "")
        chapter = ""
        for idx, line_idx in enumerate(breaks):
            start = lines[line_idx][0]
            end = lines[breaks[idx + 1]][0] if idx + 1 < len(breaks) else len(body)
            head = lines[line_idx][1].strip()
            if CHAPTER_RE.match(head):
                chapter = head
                push(start, end, "章", "", chapter)
            else:
                push(start, end, "条", ARTICLE_RE.match(head).group(0), chapter)
        return clauses

    current_start = None
    current_type = "段落"
    for _, (offset, raw) in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if current_start is None:
            current_start = offset
            current_type = "附件" if APPENDIX_RE.match(line) else "段落"
            continue
        # 够长且是完整句子 → 起新段；短行（落款、机关名、附件名）粘到上一段
        if len(line) >= 25 and line.endswith(("。", "；", "：", "！", "？")):
            end = offset
            push(current_start, end, current_type, "", "")
            current_start = offset
            current_type = "附件" if APPENDIX_RE.match(line) else "段落"
    if current_start is not None:
        push(current_start, len(body), current_type, "", "")
    return clauses


def split_docs(doc_ids: list[int] | None = None, log=None, db_path: str | Path | None = None) -> dict:
    """把暂存库里的政策正文切块写入 policy_clause；重复执行按 (doc_id, version, seq) 幂等。"""
    conn = connect(db_path)
    if doc_ids:
        rows = conn.execute(
            "SELECT id,title,doc_no,body_text,content_hash FROM policy_staging WHERE id IN (%s)"
            % ",".join("?" * len(doc_ids)),
            doc_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,title,doc_no,body_text,content_hash FROM policy_staging ORDER BY id"
        ).fetchall()
    total = 0
    for doc_id, title, doc_no, body, chash in rows:
        version = (chash or "")[:12]
        clauses = split_document(body or "")
        conn.execute("DELETE FROM policy_clause WHERE doc_id=? AND version=?", (doc_id, version))
        for c in clauses:
            conn.execute(
                """INSERT INTO policy_clause
                   (doc_id,seq,clause_no,chapter,clause_type,text,char_start,char_end,char_count,
                    version,status,publishable,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    doc_id,
                    c["seq"],
                    c["clause_no"],
                    c["chapter"],
                    c["clause_type"],
                    c["text"],
                    c["char_start"],
                    c["char_end"],
                    len(c["text"]),
                    version,
                    "pending_verify",
                    0,
                    now_utc(),
                ),
            )
        total += len(clauses)
        if log:
            log(f"[切块] {doc_no} {len(clauses)} 条 | {title[:32]}")
    conn.commit()
    conn.close()
    return {"docs": len(rows), "clauses": total}


def verify_clause_spans() -> dict:
    """逐条校验 char_start/char_end 能否精确切回条文正文。"""
    conn = connect()
    bad = []
    checked = 0
    for cid, doc_id, start, end, text in conn.execute(
        "SELECT c.id,c.doc_id,c.char_start,c.char_end,c.text FROM policy_clause c "
        "JOIN policy_staging p ON p.id=c.doc_id"
    ):
        body = conn.execute("SELECT body_text FROM policy_staging WHERE id=?", (doc_id,)).fetchone()[0] or ""
        checked += 1
        if body[start:end].strip() != text:
            bad.append({"clause_id": cid, "doc_id": doc_id, "start": start, "end": end})
    conn.close()
    return {"checked": checked, "bad": bad, "ok": checked - len(bad)}


def export_clauses(out_path: Path | None = None) -> Path:
    out_path = out_path or (DATA_DIR / "clause_index.md")
    conn = connect()
    rows = conn.execute(
        """SELECT p.doc_no,p.layer,p.title,c.seq,c.clause_no,c.clause_type,c.char_count,c.text
           FROM policy_clause c JOIN policy_staging p ON p.id=c.doc_id
           ORDER BY p.id,c.seq"""
    ).fetchall()
    docs = conn.execute("SELECT COUNT(DISTINCT doc_id) FROM policy_clause").fetchone()[0]
    conn.close()
    lines = [
        "# 政策条款切块清单（草稿）",
        "",
        f"导出时间：{now_utc()}；{docs} 份文件、{len(rows)} 条条款；**全部 pending_verify，文号未核前不可发布**（I-7）。",
        "",
        "| 文号 | 层级 | 序号 | 条号 | 类型 | 字数 | 开头 30 字 |",
        "|---|---|---|---|---|---|---|",
    ]
    for doc_no, layer, _title, seq, clause_no, ctype, clen, text in rows:
        snippet = text[:30].replace("|", "／").replace("\n", " ")
        cells = [doc_no or "", layer or "", str(seq), clause_no or "", ctype, str(clen), snippet]
        lines.append("| " + " | ".join(cells) + " |")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def export_index(out_path: Path | None = None) -> Path:
    """把暂存库导出为 Markdown 清单，便于人工逐条核对文号与生效日期。"""
    out_path = out_path or (DATA_DIR / "policy_index.md")
    conn = connect()
    rows = conn.execute(
        "SELECT layer,title,doc_no,publish_date,effective_from,length(body_text),status,note,url "
        "FROM policy_staging ORDER BY layer, publish_date DESC"
    ).fetchall()
    conn.close()
    lines = [
        "# 政策采集暂存清单（草稿）",
        "",
        f"导出时间：{now_utc()}；共 {len(rows)} 条；**全部为 pending_verify，文号与生效日期须人工核对后才可写入知识库**。",
        "",
        "| # | 层级 | 标题 | 文号 | 发布 | 施行 | 正文字数 | 状态 | 备注 | 来源 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for idx, (layer, title, doc_no, publish, eff, blen, status, note, url) in enumerate(rows, 1):
        cells = [str(idx), layer or "", title or "", doc_no or "", publish or "", eff or "", str(blen), status, note or "", url]
        cells = [c.replace("|", "／").replace("\n", " ") for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="资助政策官方文件采集（低频、可审计、只采公开栏目页）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("discover", help="查看栏目页的兄弟栏目与条目数")
    p1.add_argument("--list", required=True)
    p1.add_argument("--include", default="", help="标题关键词过滤")

    p2 = sub.add_parser("crawl", help="采集栏目页下的详情页（可一次给多个栏目或整站预设）")
    p2.add_argument("--list", nargs="+", default=None, help="一个或多个栏目页 URL")
    p2.add_argument("--preset", default="", choices=["", "国家", "广东省级"], help="按预设批量走整站兄弟栏目")
    p2.add_argument("--limit", type=int, default=5, help="每个栏目的新增上限（已采过的不占额度）")
    p2.add_argument("--layer", default="国家")
    p2.add_argument("--delay", type=float, default=3.0)
    p2.add_argument("--include", default="", help="标题关键词过滤，如 资助")
    p2.add_argument("--force", action="store_true", help="重取已采条目，内容变化才更新")
    p2.add_argument("--browser-ua", action="store_true", help="改用浏览器 UA（仅在普通 UA 被拒时使用）")

    p3 = sub.add_parser("gui", help="固定窗口点击采集")
    p3.add_argument("--list", default="https://www.xszz.edu.cn/n39/n57/index.html")

    p4 = sub.add_parser("stats", help="查看暂存库统计")
    p5 = sub.add_parser("export", help="导出 Markdown 清单供人工核对")
    p5.add_argument("--out", default="")

    p6 = sub.add_parser("split", help="把政策正文切成条款入库")
    p6.add_argument("--doc-id", nargs="*", type=int, default=None, help="只切指定 id；缺省切全部")
    p7 = sub.add_parser("clauses", help="查看某份文件的条款")
    p7.add_argument("--doc-id", type=int, required=True)
    p7.add_argument("--limit", type=int, default=20)
    p8 = sub.add_parser("verify-clauses", help="校验条款字符偏移能否切回原文")
    p9 = sub.add_parser("export-clauses", help="导出条款清单")
    p9.add_argument("--out", default="")
    args = parser.parse_args()

    if args.cmd == "discover":
        print(json.dumps(discover(args.list), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "crawl":
        if args.preset:
            columns = COLUMN_PRESETS[args.preset]
        elif args.list:
            columns = [(u, u, args.layer) for u in args.list]
        else:
            parser.error("--list 与 --preset 至少给一个")
            return 2
        result = crawl_many(
            columns, args.limit, args.delay, args.browser_ua, lambda m: print(m, flush=True), args.include, args.force
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.cmd == "gui":
        return gui(args.list)
    if args.cmd == "stats":
        conn = connect()
        rows = conn.execute("SELECT status, COUNT(*) FROM policy_staging GROUP BY status").fetchall()
        clause_rows = conn.execute("SELECT COUNT(*), COUNT(DISTINCT doc_id) FROM policy_clause").fetchone()
        runs = conn.execute("SELECT COUNT(*) FROM crawl_run").fetchone()[0]
        print(
            json.dumps(
                {
                    "db": str(DB_PATH),
                    "policy_by_status": rows,
                    "clause_total": clause_rows[0],
                    "clause_docs": clause_rows[1],
                    "crawl_runs": runs,
                },
                ensure_ascii=False,
            )
        )
        conn.close()
        return 0
    if args.cmd == "export":
        path = export_index(Path(args.out) if args.out else None)
        print(str(path))
        return 0
    if args.cmd == "split":
        result = split_docs(args.doc_id or None, log=lambda m: print(m, flush=True))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.cmd == "clauses":
        conn = connect()
        for row in conn.execute(
            "SELECT seq,clause_no,clause_type,char_count,char_start,char_end,text FROM policy_clause"
            " WHERE doc_id=? ORDER BY seq LIMIT ?",
            (args.doc_id, args.limit),
        ):
            print(f"{row[0]:>3} {row[1] or '—':<6} {row[2]:<4} {row[3]:>5}字 [{row[4]}:{row[5]}] {row[6][:40]}")
        conn.close()
        return 0
    if args.cmd == "verify-clauses":
        print(json.dumps(verify_clause_spans(), ensure_ascii=False))
        return 0
    if args.cmd == "export-clauses":
        print(str(export_clauses(Path(args.out) if args.out else None)))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
