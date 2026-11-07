"""问答评测：50 条标注集 → 命中／拒答／引用可回点／越界措辞／边界不判资格／耗时。

用法：
  python -X utf8 backend\\tools\\qa_eval.py
  python -X utf8 backend\\tools\\qa_eval.py --json --report docs\\eval\\qa_eval_report.md

判定口径（对齐 docs/ai-contract.md）：
- hit：应命中，且至少 1 条引用；
- boundary：应命中，但解释部分不得出现"符合/不符合/通过认定"或"你可以申请"等判定语气；
- refuse：应拒答；
- 引用有效：clause_id 存在，且 text[char_start:char_end] 与引用片段逐字一致、文号一致。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app import qa  # noqa: E402

QUOTE_LINE = re.compile(r"^\s*\d+\.\s*《")


class Checker:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.c = sqlite3.connect(str(db_path))
        self.c.row_factory = sqlite3.Row

    def citation_ok(self, cit: dict) -> tuple[bool, str]:
        row = self.c.execute(
            "SELECT c.text,c.char_start,c.char_end,c.clause_no,p.doc_no"
            " FROM policy_clause c JOIN policy_staging p ON p.id=c.doc_id WHERE c.id=?",
            (cit["clause_id"],)).fetchone()
        if not row:
            return False, "clause 不存在"
        if row["char_start"] != cit["span"][0] or row["char_end"] != cit["span"][1]:
            return False, "偏移不一致"
        if row["doc_no"] != cit["doc_no"]:
            return False, "文号不一致"
        return True, "ok"

    def close(self):
        self.c.close()


def explanation(answer: str, hits: list[dict]) -> str:
    """去掉条款摘录行与引用原文，只留系统自己的解释措辞。"""
    text = "\n".join(line for line in answer.splitlines() if not QUOTE_LINE.match(line))
    for h in hits:
        quoted = " ".join(h.get("quote", "")[:180].split())
        if quoted:
            text = text.replace(quoted, "")
    return text


def run(set_path: Path, policy_db: Path) -> dict:
    data = json.loads(set_path.read_text(encoding="utf-8"))
    cases = data["cases"]
    forbidden = data.get("forbidden_words", [])
    judgments = data.get("judgment_phrases", [])
    checker = Checker(policy_db)
    results, lat = [], []
    for case in cases:
        t0 = time.perf_counter()
        got = qa.answer(case["question"], db_path=policy_db)
        lat.append((time.perf_counter() - t0) * 1000)
        cits = got.get("citations") or []
        bad_cit = [checker.citation_ok(c)[1] for c in cits if not checker.citation_ok(c)[0]]
        expl = explanation(got.get("answer") or "", got.get("hits") or [])
        bad_words = [w for w in forbidden if w in expl]
        bad_judge = [w for w in judgments if w in expl]
        typ = case["type"]
        if typ == "hit":
            ok = (not got["refused"]) and len(cits) >= 1 and not bad_cit and not bad_words
        elif typ == "boundary":
            ok = (not got["refused"]) and len(cits) >= 1 and not bad_cit and not bad_judge and not bad_words
        else:
            ok = bool(got["refused"])
        results.append({"id": case["id"], "type": typ, "question": case["question"],
                        "refused": got["refused"], "citations": len(cits),
                        "confidence": got.get("confidence"), "mode": got.get("mode"),
                        "ok": ok, "bad_citation": bad_cit, "bad_words": bad_words,
                        "bad_judgment": bad_judge})
    checker.close()

    def rate(typ):
        rows = [r for r in results if r["type"] == typ]
        good = [r for r in rows if r["ok"]]
        return len(good), len(rows)

    hit_good, hit_all = rate("hit")
    b_good, b_all = rate("boundary")
    r_good, r_all = rate("refuse")
    cits = sum(r["citations"] for r in results)
    bad_cits = sum(len(r["bad_citation"]) for r in results)
    bad_words = sum(len(r["bad_words"]) for r in results)
    bad_judge = sum(len(r["bad_judgment"]) for r in results)
    lat_sorted = sorted(lat)
    p95 = lat_sorted[int(len(lat_sorted) * 0.95) - 1] if lat_sorted else 0
    metrics = {
        "total": len(results),
        "hit": f"{hit_good}/{hit_all}",
        "boundary": f"{b_good}/{b_all}",
        "refuse": f"{r_good}/{r_all}",
        "citation_total": cits,
        "citation_invalid": bad_cits,
        "forbidden_in_explanation": bad_words,
        "judgment_phrases": bad_judge,
        "mode": results[0]["mode"] if results else None,
        "prompt_version": qa.provider.PROMPT_VERSION,
        "latency_ms_avg": round(sum(lat) / len(lat), 1),
        "latency_ms_p95": round(p95, 1),
    }
    passed = (hit_good == hit_all and b_good == b_all and r_good == r_all
              and bad_cits == 0 and bad_words == 0 and bad_judge == 0)
    return {"metrics": metrics, "passed": passed,
            "failures": [r for r in results if not r["ok"]], "results": results}


def report_md(res: dict, set_path: Path) -> str:
    m = res["metrics"]
    lines = [
        "# 问答评测报告（qa_v1 离线规则模式）", "",
        f"- 评测集：`{set_path.relative_to(ROOT).as_posix()}`（50 条：35 应命中／10 边界／5 应拒答）",
        f"- 运行模式：`{m['mode']}`　提示词版本：`{m['prompt_version']}`",
        f"- 命中：{m['hit']}　边界不判定：{m['boundary']}　拒答：{m['refuse']}",
        f"- 引用条数：{m['citation_total']}，偏移/文号不一致：{m['citation_invalid']}",
        f"- 解释部分出现禁用措辞：{m['forbidden_in_explanation']}　判定语气：{m['judgment_phrases']}",
        f"- 单条耗时：平均 {m['latency_ms_avg']} ms，P95 {m['latency_ms_p95']} ms",
        f"- 结论：{'PASS' if res['passed'] else 'FAIL'}", "",
        "## 失败用例", "",
    ]
    if not res["failures"]:
        lines.append("无。")
    else:
        lines += ["| ID | 类型 | 问题 | 引用 | 原因 |", "|---|---|---|---|---|"]
        for f in res["failures"]:
            why = "；".join(filter(None, [
                "应命中未命中" if f["type"] != "refuse" and f["refused"] else "",
                f"引用无效 {f['bad_citation']}" if f["bad_citation"] else "",
                f"禁用词 {f['bad_words']}" if f["bad_words"] else "",
                f"判定语气 {f['bad_judgment']}" if f["bad_judgment"] else "",
            ])) or "未达预期"
            lines.append(f"| {f['id']} | {f['type']} | {f['question']} | {f['citations']} | {why} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "data" / "eval" / "qa_50.json"))
    ap.add_argument("--policy-db", default=str(ROOT / "data" / "staging" / "policies.db"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    res = run(Path(args.set), Path(args.policy_db))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report_md(res, Path(args.set)), encoding="utf-8")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(report_md(res, Path(args.set)))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
