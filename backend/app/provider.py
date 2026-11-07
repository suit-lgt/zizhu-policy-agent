"""三态 Provider：offline（离线规则）／online（OpenAI 兼容）／mock（自测）。

契约见 docs/ai-contract.md：
- offline 为默认模式，不联网、不调模型，用模板把条款拼成说明，功能不缩减；
- online 需 `FUND_LLM_BASE`／`FUND_LLM_KEY`／`FUND_LLM_MODEL`，输出必须过结构校验与业务校验；
  失败重试 1 次，仍失败回退 offline 并记录 last_error；
- 页面与导出件必须标注当前模式；离线模式不得伪装成在线。

自测：`python -X utf8 -m app.provider --selftest`
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "qa_v1.md"
PROMPT_VERSION = "qa_v1"

MODES = ("offline", "online", "mock")
FORBIDDEN = ("不符合", "符合", "通过认定")  # 契约：只出可申请／需核实

_last: dict = {"error": None, "at": None}
_counts = {"calls": 0, "online": 0, "fallbacks": 0, "mock": 0}

OFFLINE_NOTE = "当前为离线规则模式（未调用模型），答案只做条款摘录。"


def mode() -> str:
    m = (os.environ.get("FUND_LLM_MODE") or "offline").strip().lower()
    return m if m in MODES else "offline"


def base_url() -> str:
    return (os.environ.get("FUND_LLM_BASE") or "").rstrip("/")


def model_name() -> str:
    return os.environ.get("FUND_LLM_MODEL") or ""


def configured() -> bool:
    return bool(base_url() and os.environ.get("FUND_LLM_KEY") and model_name())


def status() -> dict:
    return {
        "mode": mode(),
        "requested_mode": os.environ.get("FUND_LLM_MODE") or "offline",
        "configured": configured(),
        "base_configured": bool(base_url()),
        "key_configured": bool(os.environ.get("FUND_LLM_KEY")),
        "model": model_name() or None,
        "prompt_version": PROMPT_VERSION,
        "last_error": _last["error"],
        "last_error_at": _last["at"],
        "count_calls": _counts["calls"],
        "count_online": _counts["online"],
        "count_fallbacks": _counts["fallbacks"],
        "mode_note": OFFLINE_NOTE if mode() == "offline" else "在线模型模式：输出经结构与业务校验，失败回退离线",
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def offline_answer(hits: list[dict]) -> str:
    lines = [f"{i + 1}. 《{h['policy_title']}》（{h['doc_no']}）{h['article_no']}："
             f"{' '.join(h['quote'][:180].split())}"
             for i, h in enumerate(hits)]
    return ("根据以下政策条款：\n" + "\n".join(lines) +
            "\n以上为条款摘录，是否适用于你需结合自身情况由学校认定。" + OFFLINE_NOTE)


def validate(text: str, hits: list[dict]) -> tuple[bool, str]:
    """结构＋业务校验：长度、禁用词、必须提到本次检索到的文号。"""
    if not isinstance(text, str) or not (20 <= len(text.strip()) <= 1200):
        return False, "长度不合规"
    for word in FORBIDDEN:
        if word in text:
            return False, f"出现禁用措辞：{word}"
    doc_nos = [h.get("doc_no") for h in hits if h.get("doc_no")]
    if doc_nos and not any(d in text for d in doc_nos):
        return False, "未引用任何本次检索到的文号"
    return True, "ok"


def _prompt_text() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return f"# {PROMPT_VERSION}\n只依据给定条款作答，禁止判定资格，禁止编造条款号。"


def _call_online(question: str, hits: list[dict], timeout: float) -> str:
    clauses = "\n".join(f"[{h['clause_id']}] 《{h['policy_title']}》（{h['doc_no']}）"
                        f"{h['article_no']}：{h['quote'][:600]}" for h in hits)
    payload = {
        "model": model_name(),
        "temperature": 0,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": _prompt_text()},
            {"role": "user", "content": f"条款：\n{clauses}\n\n学生提问：{question}"},
        ],
    }
    req = urllib.request.Request(
        base_url() + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('FUND_LLM_KEY', '')}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    if content.startswith("{"):
        content = json.loads(content).get("answer", "")
    return content


def compose(question: str, hits: list[dict]) -> dict:
    """返回 {text, mode, model, fallback, error}；离线或失败一律给出可用文案。"""
    _counts["calls"] += 1
    m = mode()
    if m == "mock":
        _counts["mock"] += 1
        return {"text": offline_answer(hits), "mode": "mock", "model": "mock",
                "fallback": False, "error": None}
    if m != "online" or not configured():
        return {"text": offline_answer(hits), "mode": "offline", "model": None,
                "fallback": m == "online", "error": None if m == "offline" else "在线模式缺少配置，已回退离线"}
    timeout = float(os.environ.get("FUND_LLM_TIMEOUT", "20"))
    last_why = ""
    for attempt in range(2):  # 首次 + 重试 1 次
        try:
            text = _call_online(question, hits, timeout)
            ok, why = validate(text, hits)
            if ok:
                _counts["online"] += 1
                return {"text": text, "mode": "online", "model": model_name(),
                        "fallback": False, "error": None}
            last_why = why
        except Exception as exc:  # 网络、鉴权、结构异常都走回退
            last_why = f"{type(exc).__name__}: {exc}"
    _counts["fallbacks"] += 1
    _last.update({"error": last_why or "校验失败", "at": _now()})
    return {"text": offline_answer(hits), "mode": "offline", "model": None,
            "fallback": True, "error": _last["error"]}


def _selftest() -> int:
    hits = [{"clause_id": 351, "article_no": "第十一条", "quote": "国家助学金平均资助标准为每生每年3700元。",
             "policy_title": "广东省学生资助资金管理实施办法", "doc_no": "粤财规〔2026〕1号"}]
    ok = 0
    text = offline_answer(hits)
    ok += 1 if ("粤财规〔2026〕1号" in text and "离线规则模式" in text) else 0
    checks = [
        ("离线文案合规", validate(text, hits)[0] is True),
        ("拒绝判定资格", validate("你符合条件，可以申请", hits)[0] is False),
        ("拒绝编造文号", validate("根据某文件规定，可以申请。", hits)[0] is False),
    ]
    for name, passed in checks:
        print(("PASS" if passed else "FAIL"), name)
        ok += 1 if passed else 0
    os.environ["FUND_LLM_MODE"] = "online"
    os.environ.pop("FUND_LLM_BASE", None)
    res = compose("助学金标准", hits)
    fallback_ok = res["mode"] == "offline" and res["fallback"] is True and "粤财规" in res["text"]
    print(("PASS" if fallback_ok else "FAIL"), "在线缺配置回退离线")
    ok += 1 if fallback_ok else 0
    print(f"{ok}/5 PASS")
    return 0 if ok == 5 else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(json.dumps(status(), ensure_ascii=False, indent=2))
