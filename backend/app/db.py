"""业务库：建表、种子、审计。audit_log 在数据库层禁止改写与删除（I-5）。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DB = Path(os.environ.get("APP_DB", str(ROOT / "data" / "app.db")))
PROJECTS = ROOT / "data" / "projects.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
  user_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS triage_task (
  task_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, owner TEXT,
  policy_version TEXT, policy_verified INTEGER NOT NULL DEFAULT 0,
  student_json TEXT NOT NULL, result_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'triaged', consent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS review_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, project_id TEXT,
  action TEXT NOT NULL, actor TEXT, role TEXT, reason TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appeal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, project_id TEXT,
  reason TEXT, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL,
  handler TEXT, handled_at TEXT, decision TEXT, decision_reason TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, role TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_verify (
  doc_id INTEGER PRIMARY KEY, doc_no TEXT, issuer TEXT, effective_from TEXT,
  verifier TEXT, verified_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS policy_version (
  version_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, published_by TEXT,
  label TEXT, docs INTEGER, clauses INTEGER, atoms INTEGER,
  status TEXT NOT NULL, snapshot_json TEXT
);
CREATE TABLE IF NOT EXISTS material_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, name TEXT NOT NULL,
  source_type TEXT NOT NULL, source_clause_id INTEGER, required INTEGER NOT NULL DEFAULT 1,
  note TEXT
);
CREATE TABLE IF NOT EXISTS school_tier (
  id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, income_band TEXT,
  amount INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS export_request (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, requester TEXT, role TEXT,
  note TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
  reviewer TEXT, reviewed_at TEXT, review_note TEXT
);
CREATE TABLE IF NOT EXISTS user_session (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
"""

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(APP_DB)
    c.row_factory = sqlite3.Row
    return c


def init_schema() -> None:
    APP_DB.parent.mkdir(parents=True, exist_ok=True)
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(TRIGGERS)
    # 兼容旧库：补列
    cols = {r["name"] for r in c.execute("PRAGMA table_info(triage_task)")}
    for name, ddl in (("owner", "TEXT"), ("consent", "INTEGER NOT NULL DEFAULT 0")):
        if name not in cols:
            c.execute(f"ALTER TABLE triage_task ADD COLUMN {name} {ddl}")
    ucols = {r["name"] for r in c.execute("PRAGMA table_info(app_user)")}
    for name, ddl in (("enabled", "INTEGER NOT NULL DEFAULT 1"), ("updated_at", "TEXT"),
                      ("nickname", "TEXT"), ("password_hash", "TEXT"), ("school", "TEXT"),
                      ("phone", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'active'"),
                      ("created_at", "TEXT"), ("reviewed_by", "TEXT"), ("reviewed_at", "TEXT"),
                      ("review_note", "TEXT"), ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
                      ("last_login_at", "TEXT")):
        if name not in ucols:
            c.execute(f"ALTER TABLE app_user ADD COLUMN {name} {ddl}")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_nickname ON app_user(nickname)"
              " WHERE nickname IS NOT NULL")
    c.commit()
    c.close()

    from .qa_sessions import init_schema as init_qa_schema
    init_qa_schema()


def user_enabled(user_id: str) -> bool:
    """账号是否启用；库里没有的账号按启用处理（演示态 X-User 可自定义）。"""
    c = conn()
    row = c.execute("SELECT enabled FROM app_user WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    return bool(row["enabled"]) if row else True


def seed() -> dict:
    c = conn()
    users = [
        ("student_demo", "示例学生", "student"),
        ("counselor_demo", "示例辅导员", "counselor"),
        ("admin_demo", "示例资助中心管理员", "admin"),
        ("auditor_demo", "示例审计员", "auditor"),
    ]
    for u in users:
        c.execute("INSERT OR IGNORE INTO app_user(user_id,name,role) VALUES(?,?,?)", u)
        c.execute("UPDATE app_user SET nickname=?,status='active',enabled=1"
                  " WHERE user_id=? AND nickname IS NULL", (u[0], u[0]))

    # 材料：把通用清单显式标为"学校要求"，不再假装是政策条款
    have = c.execute("SELECT COUNT(*) FROM material_item").fetchone()[0]
    if have == 0:
        projects = json.loads(PROJECTS.read_text(encoding="utf-8"))
        for p in projects["projects"]:
            for name in p.get("materials", []):
                c.execute(
                    "INSERT INTO material_item(project_id,name,source_type,source_clause_id,required,note)"
                    " VALUES(?,?,?,?,?,?)",
                    (p["code"], name, "学校要求", None, 1, "通用办理清单，以学校资助中心要求为准"),
                )
    c.commit()
    counts = {
        "users": c.execute("SELECT COUNT(*) FROM app_user").fetchone()[0],
        "materials": c.execute("SELECT COUNT(*) FROM material_item").fetchone()[0],
        "tiers": c.execute("SELECT COUNT(*) FROM school_tier").fetchone()[0],
        "tasks": c.execute("SELECT COUNT(*) FROM triage_task").fetchone()[0],
    }
    c.close()
    return counts


def audit(actor: str, role: str, action: str, target: str = "", detail: dict | None = None) -> None:
    """写审计；target 与 detail 落库前脱敏（I-4：日志不记家庭明细）。"""
    from . import logs  # 局部导入，避免模块成环

    c = conn()
    c.execute(
        "INSERT INTO audit_log(actor,role,action,target,detail,created_at) VALUES(?,?,?,?,?,?)",
        (actor, role, action, logs.redact_text(target),
         json.dumps(logs.redact_obj(detail or {}), ensure_ascii=False), now()),
    )
    c.commit()
    c.close()


if __name__ == "__main__":
    init_schema()
    print(json.dumps(seed(), ensure_ascii=False))
