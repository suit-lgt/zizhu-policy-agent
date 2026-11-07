"""事实支持核验：逐条检查现行版本原子能否回点条款原文，并导出人工复核清单。

判定项（机器可验证）：
1. 原子在版本快照内且 superseded=0；
2. 条款在版本快照内、文号一致；
3. 引文片段能在条款原文按绝对偏移精确切出（quote_start/quote_end 与条款 char_start/char_end 同坐标系）；
4. 条款来源文件为 verified/published。

人工项：引文是否在语义上支持该原子（导出 CSV 留空列，由资助中心签认）。
退出码 0 表示机器项全部通过，不代表人工项已完成。
"""
import argparse
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "staging" / "policies.db"))
    ap.add_argument("--version", default="")
    ap.add_argument("--csv", default=str(ROOT / "docs" / "eval" / "fact_support_review.csv"))
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    c.row_factory = sqlite3.Row
    vid = a.version
    if not vid:
        app = sqlite3.connect(str(ROOT / "data" / "app.db"))
        row = app.execute("SELECT version_id FROM policy_version WHERE status='current'"
                          " ORDER BY created_at DESC LIMIT 1").fetchone()
        vid = row[0] if row else ""
        app.close()
    atoms = c.execute("SELECT * FROM policy_snapshot_atom WHERE version_id=? AND superseded=0", (vid,)).fetchall()
    clauses = {r["clause_id"]: r for r in c.execute(
        "SELECT * FROM policy_snapshot_clause WHERE version_id=?", (vid,)).fetchall()}
    docs = {r[0]: r[1] for r in c.execute("SELECT id,status FROM policy_staging").fetchall()}
    rows, bad = [], 0
    for at in atoms:
        cl = clauses.get(at["clause_id"])
        checks = {
            "原子在版本快照": True,
            "条款在版本快照": cl is not None,
            "文号一致": bool(cl) and (at["doc_no"] or "") == (cl["doc_no"] or ""),
            "来源已核文": docs.get(at["doc_id"]) in ("verified", "published"),
        }
        quote_ok = False
        if cl:
            s, e = at["quote_start"], at["quote_end"]
            if s is not None and e is not None:
                rel_s, rel_e = s - cl["char_start"], e - cl["char_start"]
                quote_ok = 0 <= rel_s < rel_e <= len(cl["text"]) and \
                    cl["text"][rel_s:rel_e].replace(" ", "") == (at["quote"] or "").replace(" ", "")
        checks["引文可按偏移切出"] = quote_ok
        passed = all(checks.values())
        bad += 0 if passed else 1
        rows.append({
            "atom_id": at["atom_id"], "project_code": at["project_code"], "field": at["field"],
            "op": at["op"], "value": at["value"], "role": at["role"],
            "doc_no": at["doc_no"], "clause_id": at["clause_id"],
            "quote": (at["quote"] or "")[:80],
            "machine_verdict": "PASS" if passed else "FAIL",
            "failed_checks": "；".join(k for k, v in checks.items() if not v),
            "human_sign": "", "human_note": "",
        })
    out = Path(a.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["atom_id"])
        w.writeheader()
        w.writerows(rows)
    print(f"版本 {vid}：原子 {len(atoms)} 条，机器核验通过 {len(atoms)-bad}，失败 {bad}")
    print(f"人工复核清单：{out}")
    for r in rows:
        if r["machine_verdict"] != "PASS":
            print("FAIL", r["atom_id"], r["failed_checks"])
    c.close()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
