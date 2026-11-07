"""初始化业务库：建表、写种子账号与材料来源、核对政策库是否就绪。

用法：python -X utf8 backend\scripts\init_db.py
幂等：可重复执行；不会清空已有任务与审计。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402


def check_policy_db() -> dict:
    path = ROOT / "data" / "staging" / "policies.db"
    if not path.exists():
        return {"exists": False, "docs": 0, "clauses": 0}
    c = sqlite3.connect(str(path))
    docs = c.execute("SELECT COUNT(*) FROM policy_staging").fetchone()[0]
    clauses = c.execute("SELECT COUNT(*) FROM policy_clause").fetchone()[0]
    c.close()
    return {"exists": True, "docs": docs, "clauses": clauses}


def main() -> int:
    db.init_schema()
    counts = db.seed()
    policy = check_policy_db()
    report = {
        "app_db": str(db.APP_DB), "app_tables": counts,
        "policy_db": policy,
        "hint": "政策库缺失时先跑 backend/tools/policy_collector.py crawl 与 split",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = policy["exists"] and policy["docs"] > 0 and counts["materials"] > 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
