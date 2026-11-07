"""隐私自查：扫描业务库中手机号／身份证／银行卡／邮箱与姓名键残留。

用法：python -X utf8 backend\\scripts\\privacy_check.py
退出码：0 表示未发现疑似明文；1 表示发现（输出只给位置与命中类型，不回显原值）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.logs import BANK, EMAIL, ID18, PHONE, SENSITIVE_KEYS  # noqa: E402

APP_DB = ROOT / "data" / "app.db"


def scan_text(text: str) -> list[str]:
    hits = []
    for name, pat in (("id18", ID18), ("phone", PHONE), ("bank", BANK), ("email", EMAIL)):
        if pat.search(text or ""):
            hits.append(name)
    return hits


def scan_json(obj) -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(t in str(k).lower() for t in SENSITIVE_KEYS) and v not in (None, "", "***"):
                hits.append(f"name_key:{k}")
            hits += scan_json(v)
    elif isinstance(obj, list):
        for x in obj:
            hits += scan_json(x)
    elif isinstance(obj, str):
        hits += scan_text(obj)
    return hits


def main() -> int:
    if not APP_DB.exists():
        print(f"NG 业务库不存在：{APP_DB}")
        return 1
    c = sqlite3.connect(str(APP_DB))
    c.row_factory = sqlite3.Row
    findings = []
    for row in c.execute("SELECT id,actor,action,target,detail FROM audit_log").fetchall():
        hits = scan_text(row["target"]) + scan_json(json.loads(row["detail"] or "{}"))
        if hits:
            findings.append(("audit_log", row["id"], sorted(set(hits))))
    for row in c.execute("SELECT task_id,owner,student_json FROM triage_task").fetchall():
        hits = scan_json(json.loads(row["student_json"] or "{}"))
        if hits:
            findings.append(("triage_task", row["task_id"], sorted(set(hits))))
    total_audit = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    total_tasks = c.execute("SELECT COUNT(*) FROM triage_task").fetchone()[0]
    c.close()
    print(f"扫描 audit_log {total_audit} 行、triage_task {total_tasks} 行")
    if findings:
        print(f"发现 {len(findings)} 处疑似明文：")
        for table, key, types in findings:
            print(f"  - {table} {key}: {', '.join(types)}")
        print("NG：命中内容未回显，请按 I-4 脱敏后重跑")
        return 1
    print("PASS：未发现手机号／身份证／银行卡／邮箱／姓名键明文")
    return 0


if __name__ == "__main__":
    sys.exit(main())
