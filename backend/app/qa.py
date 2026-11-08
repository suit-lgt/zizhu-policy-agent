"""政策问答：检索、拼装、拒答（供接口与评测共用，避免两套逻辑）。

- 检索：只查“当前发布版本”的条款快照，未核文、未发布、旧版本一律不可见；
- 回答：offline 模板／online 模型文案，引用与条数由程序拼装；
- 拒答：无命中只给线下渠道，不作答；
- 置信：命中 ≥2 条为 high，1 条为 low。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import provider

ROOT = Path(__file__).resolve().parents[2]
POLICY_DB = Path(os.environ.get("POLICY_DB", str(ROOT / "data" / "staging" / "policies.db")))
APP_DB = Path(os.environ.get("APP_DB", str(ROOT / "data" / "app.db")))
DISCLAIMER = "AI 初判仅供参考，最终以资助部门认定为准；政策文号未核，本结果仅供内部验证。"
REFUSAL = ("当前政策版本里没有检索到可引用的条款，我不作答。建议联系学校学生资助中心（线下）"
           "或在学校学生资助管理系统提交咨询。")
OFF_DOMAIN = ("火星", "天气", "论文", "股票", "基金", "彩票", "星座", "王者荣耀",
              "游戏", "上分", "小说", "翻译", "编程")

SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS policy_snapshot_clause (
  version_id TEXT NOT NULL,
  clause_id INTEGER NOT NULL,
  doc_id INTEGER NOT NULL,
  doc_no TEXT,
  title TEXT,
  url TEXT,
  clause_no TEXT,
  text TEXT NOT NULL,
  char_start INTEGER,
  char_end INTEGER,
  created_at TEXT,
  PRIMARY KEY (version_id, clause_id)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_clause_text ON policy_snapshot_clause(version_id, text);
"""

SNAPSHOT_ATOM_DDL = """
CREATE TABLE IF NOT EXISTS policy_snapshot_atom (
  version_id TEXT NOT NULL,
  atom_id TEXT NOT NULL,
  project_code TEXT,
  doc_id INTEGER,
  clause_id INTEGER,
  doc_no TEXT,
  field TEXT,
  op TEXT,
  value TEXT,
  role TEXT,
  determinable INTEGER,
  superseded INTEGER,
  quote TEXT,
  quote_start INTEGER,
  quote_end INTEGER,
  PRIMARY KEY (version_id, atom_id)
);
"""


def conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or POLICY_DB))
    c.row_factory = sqlite3.Row
    return c


def current_version(app_db: Path | str | None = None) -> str | None:
    """当前发布版本号；没有 current 版本时返回 None。"""
    try:
        c = sqlite3.connect(str(app_db or APP_DB))
        row = c.execute("SELECT version_id FROM policy_version WHERE status='current'"
                        " ORDER BY created_at DESC LIMIT 1").fetchone()
        c.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def version_docs(version_id: str, app_db: Path | str | None = None) -> dict:
    """版本快照里的文件清单：{doc_id: {doc_no,title}}。"""
    try:
        c = sqlite3.connect(str(app_db or APP_DB))
        row = c.execute("SELECT snapshot_json FROM policy_version WHERE version_id=?", (version_id,)).fetchone()
        c.close()
    except sqlite3.Error:
        return {}
    if not row or not row[0]:
        return {}
    try:
        docs = json.loads(row[0]).get("docs") or []
    except json.JSONDecodeError:
        return {}
    return {int(d["id"]): {"doc_no": d.get("doc_no"), "title": d.get("title")} for d in docs if d.get("id")}


def ensure_snapshot(db_path: Path | str | None = None, app_db: Path | str | None = None) -> str | None:
    """为当前版本补齐条款／原子快照（幂等）。只从已核文文件生成，未核文草稿不入快照。"""
    vid = current_version(app_db)
    if not vid:
        return None
    c = conn(db_path)
    c.executescript(SNAPSHOT_DDL + SNAPSHOT_ATOM_DDL)
    has = c.execute("SELECT COUNT(*) FROM policy_snapshot_clause WHERE version_id=?", (vid,)).fetchone()[0]
    if not has:
        docs = version_docs(vid, app_db)
        if docs:
            marks = ",".join("?" for _ in docs)
            rows = c.execute(
                f"SELECT c.id,c.doc_id,c.clause_no,c.text,c.char_start,c.char_end,p.doc_no,p.title,p.url,p.status"
                f" FROM policy_clause c JOIN policy_staging p ON p.id=c.doc_id"
                f" WHERE c.doc_id IN ({marks}) AND p.status IN ('verified','published')",
                tuple(docs.keys())).fetchall()
            for r in rows:
                c.execute("INSERT OR REPLACE INTO policy_snapshot_clause"
                          "(version_id,clause_id,doc_id,doc_no,title,url,clause_no,text,char_start,char_end,created_at)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                          (vid, r["id"], r["doc_id"], r["doc_no"], r["title"], r["url"], r["clause_no"],
                           r["text"], r["char_start"], r["char_end"]))
        atom_has = c.execute("SELECT COUNT(*) FROM policy_snapshot_atom WHERE version_id=?", (vid,)).fetchone()[0]
        if not atom_has:
            for r in c.execute("SELECT * FROM policy_atom").fetchall():
                c.execute("INSERT OR REPLACE INTO policy_snapshot_atom"
                          "(version_id,atom_id,project_code,doc_id,clause_id,doc_no,field,op,value,role,"
                          "determinable,superseded,quote,quote_start,quote_end)"
                          " SELECT ?,atom_id,project_code,doc_id,clause_id,doc_no,field,op,value,role,"
                          " determinable,superseded,quote,quote_start,quote_end FROM policy_atom WHERE atom_id=?",
                          (vid, r["atom_id"]))
    c.commit()
    c.close()
    return vid


def write_snapshot(version_id: str, db_path: Path | str | None = None) -> dict:
    """发布时调用：把当前已核文文件的条款与现行原子固化到版本快照。"""
    c = conn(db_path)
    c.executescript(SNAPSHOT_DDL + SNAPSHOT_ATOM_DDL)
    c.execute("DELETE FROM policy_snapshot_clause WHERE version_id=?", (version_id,))
    c.execute("DELETE FROM policy_snapshot_atom WHERE version_id=?", (version_id,))
    rows = c.execute(
        "SELECT c.id,c.doc_id,c.clause_no,c.text,c.char_start,c.char_end,p.doc_no,p.title,p.url,p.status"
        " FROM policy_clause c JOIN policy_staging p ON p.id=c.doc_id"
        " WHERE p.status IN ('verified','published')").fetchall()
    for r in rows:
        c.execute("INSERT OR REPLACE INTO policy_snapshot_clause"
                  "(version_id,clause_id,doc_id,doc_no,title,url,clause_no,text,char_start,char_end,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                  (version_id, r["id"], r["doc_id"], r["doc_no"], r["title"], r["url"], r["clause_no"],
                   r["text"], r["char_start"], r["char_end"]))
    atoms = c.execute("SELECT COUNT(*) FROM policy_snapshot_atom WHERE version_id=?", (version_id,)).fetchone()[0]
    if not atoms:
        c.execute("INSERT OR REPLACE INTO policy_snapshot_atom"
                  "(version_id,atom_id,project_code,doc_id,clause_id,doc_no,field,op,value,role,"
                  "determinable,superseded,quote,quote_start,quote_end)"
                  " SELECT ?,atom_id,project_code,doc_id,clause_id,doc_no,field,op,value,role,"
                  " determinable,superseded,quote,quote_start,quote_end FROM policy_atom",
                  (version_id,))
    stat = {"clauses": len(rows),
            "atoms": c.execute("SELECT COUNT(*) FROM policy_snapshot_atom WHERE version_id=?", (version_id,)).fetchone()[0]}
    c.commit()
    c.close()
    from .qa_evidence import snapshot_metadata
    snapshot_metadata(version_id, db_path)
    return stat


def has_snapshot(version_id: str, db_path: Path | str | None = None) -> bool:
    c = conn(db_path)
    try:
        n = c.execute("SELECT COUNT(*) FROM policy_snapshot_clause WHERE version_id=?", (version_id,)).fetchone()[0]
    except sqlite3.Error:
        n = 0
    c.close()
    return n > 0


def search_clauses(q: str, limit: int = 3, db_path: Path | str | None = None,
                   version_id: str | None = None) -> list[dict]:
    """只检索当前（或指定）发布版本的条款快照；不足 2 字抛 ValueError。"""
    q = (q or "").strip()
    if len(q) < 2:
        raise ValueError("关键词至少 2 个字")
    vid = version_id or ensure_snapshot(db_path)
    if not vid:
        return []
    c = conn(db_path)
    rows = c.execute(
        "SELECT clause_id,clause_no,text,char_start,char_end,doc_no,title,url,doc_id"
        " FROM policy_snapshot_clause WHERE version_id=? AND text LIKE ?"
        " ORDER BY length(text) ASC LIMIT ?",
        (vid, f"%{q}%", limit)).fetchall()
    c.close()
    return [{"clause_id": r["clause_id"], "article_no": r["clause_no"] or "", "quote": r["text"],
             "char_start": r["char_start"], "char_end": r["char_end"],
             "doc_no": r["doc_no"], "policy_title": r["title"], "url": r["url"],
             "doc_id": r["doc_id"], "version_id": vid} for r in rows]


STOP_CHARS = set("我你他她它的了吗呢吧啊呀是在有和与及或就都也还很更最能不能可以怎么怎样如何什么哪些哪个多少几时请问一下帮")


def _gram_candidates(question: str) -> list[str]:
    """中文无空格，整句命中不了时退化为 4/3/2 字滑窗（跳过纯虚词片段）。"""
    q = "".join(ch if ("\u4e00" <= ch <= "\u9fff" or ch.isalnum()) else " " for ch in question)
    out = [q.strip()]
    for seg in q.split():
        for n in (4, 3, 2):
            for i in range(len(seg) - n + 1):
                gram = seg[i:i + n]
                if all(ch in STOP_CHARS for ch in gram):
                    continue
                out.append(gram)
    return [x for x in out if len(x) >= 2]


def find_hits(question: str, limit: int = 3, db_path: Path | str | None = None,
              version_id: str | None = None) -> list[dict]:
    """整句优先，其次 4/3/2 字片段；命中即停。"""
    for token in _gram_candidates(question):
        try:
            hits = search_clauses(token, limit, db_path, version_id)
        except ValueError:
            hits = []
        if hits:
            return hits
    return []


def answer(question: str, limit: int = 3, db_path: Path | str | None = None,
           version_id: str | None = None) -> dict:
    question = (question or "").strip()
    version_id = version_id or ensure_snapshot(db_path)
    off = [w for w in OFF_DOMAIN if w in question]
    from .qa_evidence import retrieve
    hits = [] if off else retrieve(question, {"policy_version": version_id}, db_path, limit, legacy=True)
    if not hits:
        mode = provider.mode()
        return {"refused": True, "reason": "off_domain" if off else "no_hit",
                "hits": [], "citations": [], "answer": REFUSAL,
                "confidence": "low", "mode": mode, "version_id": version_id,
                "mode_label": "离线规则模式" if mode != "online" else "在线模型模式",
                "provider": provider.status(), "prompt_version": provider.PROMPT_VERSION,
                "disclaimer": DISCLAIMER}
    res = provider.compose(question, hits)
    citations = [{"clause_id": h["clause_id"], "doc_no": h["doc_no"], "article_no": h["article_no"],
                  "span": [h["char_start"], h["char_end"]], "version_id": h["version_id"]} for h in hits]
    mode = res["mode"]
    return {"refused": False, "hits": hits, "citations": citations,
            "answer": res["text"], "confidence": "high" if len(hits) >= 2 else "low",
            "mode": mode, "model": res.get("model"), "fallback": bool(res.get("fallback")),
            "provider_error": res.get("error"), "version_id": hits[0].get("version_id"),
            "mode_label": "离线规则模式" if mode == "offline" else ("测试 Mock" if mode == "mock" else "在线模型模式"),
            "provider": provider.status(), "prompt_version": provider.PROMPT_VERSION,
            "disclaimer": DISCLAIMER}
