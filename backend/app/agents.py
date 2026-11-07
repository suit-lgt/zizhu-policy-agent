"""规则型多智能体协同：把初判拆成 6 个职责单一的确定性代理，并留下可核对的步骤留痕。

没有模型参与：每个代理都是可复现的代码逻辑（校验／判等／引用核验／措辞／合规／汇总），
所以同一输入的结果与留痕一致，便于答辩时逐步解释“谁做了什么、依据在哪”。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

AGENT_SPEC = [
    ("intake", "受理代理", "校验学籍与家庭字段、统计缺项，字段不合法直接拒绝"),
    ("rule", "判等代理", "按原子条件逐项判定，输出可申请／需核实／暂不适用三档"),
    ("cite", "依据核验代理", "逐条核对结论挂的条款是否存在、原文偏移是否可回点"),
    ("compose", "表述代理", "把结论、缺项、渠道组织成学生能读的说明（不生成引用）"),
    ("compliance", "合规代理", "扫描是否出现判定性措辞，检查免责声明与版本标注"),
    ("report", "汇总代理", "汇总步骤、耗时与统计，形成可追溯的协同记录"),
]


def _step(name: str, started: float, status: str, summary: str, extra: dict | None = None) -> dict:
    labels = {k: label for k, label, _ in AGENT_SPEC}
    idx = [k for k, _, _ in AGENT_SPEC].index(name) + 1
    return {"agent": name, "label": labels[name], "seq": idx, "status": status,
            "ms": round((time.perf_counter() - started) * 1000, 1), "summary": summary,
            **(extra or {})}


def _label_of(name: str) -> str:
    for key, label, _ in AGENT_SPEC:
        if key == name:
            return label
    return name


def run_triage(student: dict, triage_mod, policy_db: Path | str, disclaimer: str,
               version: str | None) -> dict:
    """执行 6 个代理，返回 result（含 trace 与 agents 元信息）。"""
    trace: list[dict] = []
    t0 = time.perf_counter()

    # 1 受理
    started = time.perf_counter()
    errors = triage_mod.validate_student(student)
    missing = [k for k, v in student.items() if v in (None, "", [])]
    trace.append(_step("intake", started, "fail" if errors else "ok",
                       (errors[0] if errors else f"字段合法；空值 {len(missing)} 项"),
                       {"errors": errors}))
    if errors:
        return {"error": errors[0], "trace": trace,
                "agents": {"count": len(trace), "mode": "rules", "note": "受理未通过，后续代理未执行"}}

    # 2 判等
    started = time.perf_counter()
    result = triage_mod.triage(student, version_id=version, db_path=policy_db)
    items = result.get("items", [])
    counts = {"可申请": 0, "需核实": 0, "暂不适用": 0}
    for it in items:
        if it.get("conclusion_label") in counts:
            counts[it["conclusion_label"]] += 1
    trace.append(_step("rule", started, "ok",
                       f"{len(items)} 个项目：" + "／".join(f"{k}{v}" for k, v in counts.items()),
                       {"projects": len(items), "counts": counts}))

    # 3 依据核验：真实回库比对 clause_id 与偏移
    started = time.perf_counter()
    bad: list[str] = []
    checked = 0
    c = sqlite3.connect(str(policy_db))
    c.row_factory = sqlite3.Row
    for it in items:
        for r in it.get("reasons", []):
            checked += 1
            row = c.execute("SELECT doc_id,char_start,char_end,text FROM policy_clause WHERE id=?",
                            (r["clause_id"],)).fetchone()
            if not row:
                bad.append(f"{r.get('clause_id')}:missing")
                continue
            start, end = int(r.get("char_start", -1)), int(r.get("char_end", -1))
            same_span = start == int(row["char_start"]) and end == int(row["char_end"])
            quote = r.get("quote") or ""
            rel_ok = quote and 0 <= start - int(row["char_start"]) < end - int(row["char_start"]) \
                and row["text"][start - int(row["char_start"]):end - int(row["char_start"])] == quote
            abs_ok = quote and 0 <= start < end and row["text"][start:end] == quote
            if not (same_span or rel_ok or abs_ok):
                bad.append(f"{r.get('clause_id')}:span mismatch")
    c.close()
    trace.append(_step("cite", started, "ok" if not bad else "fail",
                       f"核对 {checked} 条引用，" + ("全部可回点" if not bad else f"{len(bad)} 条异常"),
                       {"checked": checked, "bad": bad[:5]}))

    # 4 表述：生成面向学生的摘要（不含引用）
    started = time.perf_counter()
    lines = []
    for it in items:
        if it.get("conclusion_label") in ("可申请", "需核实"):
            tail = f"（缺 {'、'.join(it.get('missing_fields') or [])}）" if it.get("missing_fields") else ""
            lines.append(f"{it['project_name']}：{it['conclusion_label']}{tail}")
    summary_text = "；".join(lines) if lines else "当前没有可申请或需核实的项目。"
    trace.append(_step("compose", started, "ok", summary_text[:80],
                       {"mode": "template", "chars": len(summary_text)}))
    result["student_summary"] = summary_text

    # 5 合规：扫描判定措辞与免责声明
    started = time.perf_counter()
    forbidden = [w for w in ("符合", "不符合", "通过认定") if any(
        w in (it.get("conclusion_label", "") + it.get("explanation", "")) for it in items)]
    has_claim = "仅供参考" in (disclaimer or "")
    trace.append(_step("compliance", started, "ok" if not forbidden and has_claim else "fail",
                       "无判定措辞；免责声明已附" if not forbidden and has_claim
                       else f"命中：{', '.join(forbidden) or '缺免责声明'}",
                       {"forbidden": forbidden}))

    # 6 汇总
    started = time.perf_counter()
    result["trace"] = trace
    result["agents"] = {"count": len([t for t in trace if t["status"] == "ok"]),
                        "planned": len(AGENT_SPEC), "mode": "rules",
                        "note": "6 个确定性代理，无模型调用；每步耗时与产出见 trace"}
    result["policy_version"] = version or result.get("policy_version")
    trace.append(_step("report", started, "ok",
                       f"版本 {result.get('policy_version')}；总耗时 {round((time.perf_counter()-t0)*1000,1)}ms",
                       {"version": result.get("policy_version")}))
    result["agents"]["count"] = len([t for t in trace if t["status"] == "ok"])
    return result
