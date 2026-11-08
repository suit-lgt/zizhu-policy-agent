"""资助政策智能体：业务服务（FastAPI）。

覆盖 docs/开发文档.md 的 A—G 七个域：
- A 政策知识库：核文、发布快照、版本回滚、条款检索
- B 学生端：初判、依据条、材料来源、问答、复核申请、申诉、导出
- C 审核端：待办、复核录入、看板
- D 管理端：权限、政策配置、字典、校级分档、审计
- E AI 层：模板组织措辞（不调模型）；问答为条款检索＋拒答
- F/G：健康检查、审计不可删、最小化留存

身份：fund_session Cookie 会话（见 backend/app/auth.py）；页面不再发 X-Role／X-User。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "tools"))

import policy_collector as pc  # noqa: E402
import triage as tg  # noqa: E402

from . import accounts, agents, auth, db, identity, ingest, logs, provider, qa  # noqa: E402

STATIC = ROOT / "frontend"
FIELDS_PATH = ROOT / "data" / "fields.json"
POLICY_DB = Path(os.environ.get("POLICY_DB", str(pc.DB_PATH)))
FORM_EXCLUDE = {"standard_amount"}
DISCLAIMER = qa.DISCLAIMER

app = FastAPI(title="资助政策智能体", version="0.5-draft")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

LOG = logging.getLogger("fund.access")
if not LOG.handlers:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG.setLevel(getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO))


ENTRY_PORT_ROLES = {8000: "public", 8001: "review", 8002: "admin", 8003: "api"}
ENTRY_PAGE_REDIRECTS = {
    8000: {"/admin.html": 8002, "/review.html": 8001},
    8001: {"/admin.html": 8002},
    8002: {"/review.html": 8001},
}


def _entry_port(request) -> int | None:
    """取浏览器访问端口；Docker 映射多端口时 Host 头会保留宿主端口。"""
    host = (request.headers.get("host") or "").strip()
    try:
        return int(host.rsplit(":", 1)[1]) if ":" in host else None
    except (ValueError, IndexError):
        return None


def _cookie_path(request) -> str:
    """子路径部署时把会话 Cookie 限定在该子路径，避免泄漏给同域根站。"""
    prefix = (request.headers.get("x-forwarded-prefix") or os.environ.get("PUBLIC_BASE_PATH") or "").strip()
    if not prefix:
        return "/"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/") or "/"


ADMIN_ONLY_PREFIXES = (
    "/api/admin/", "/api/users", "/api/audit", "/api/report/", "/api/school-tiers",
    "/api/policy/docs", "/api/policy/upload", "/api/policy/versions", "/api/policy/rollback",
    "/api/export-requests/",
)
ADMIN_POLICY_SUFFIXES = ("/verify", "/split", "/publish")


def _is_admin_only(path: str) -> bool:
    if any(path.startswith(p) for p in ADMIN_ONLY_PREFIXES):
        return True
    return path.startswith("/api/policy/") and path.endswith(ADMIN_POLICY_SUFFIXES)


def _entry_blocked(port: int, path: str) -> bool:
    if port == 8000:   # 学生／公共入口：不提供管理与审核端
        return path in ("/admin.html", "/review.html") or _is_admin_only(path) \
            or path.startswith("/api/review/")
    if port == 8001:   # 教师审核入口：不提供管理端
        return path == "/admin.html" or _is_admin_only(path)
    if port == 8002:   # 管理入口：不提供审核台与学生初判接口
        return path == "/review.html" or path == "/api/triage" or path.startswith(
            ("/api/review/", "/api/board", "/api/tasks", "/api/appeals", "/api/appeal/", "/api/triage/"))
    if port == 8003:   # 接口／健康检查入口：不提供网页
        return not (path.startswith("/api/") or path in ("/docs", "/redoc", "/openapi.json"))
    return False


@app.middleware("http")
async def access_log(request, call_next):
    """端口入口约束 + 访问日志；响应体与请求体不落日志。"""
    port = _entry_port(request)
    if port in ENTRY_PORT_ROLES:
        target_port = ENTRY_PAGE_REDIRECTS.get(port, {}).get(request.url.path)
        if target_port:
            q = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(
                f"{request.url.scheme}://{request.url.hostname}:{target_port}{request.url.path}{q}",
                status_code=307)
        if _entry_blocked(port, request.url.path):
            return JSONResponse(status_code=404,
                                content={"detail": f"该端口（{port}）不提供此入口，请使用对应端端口"})
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    q = logs.redact_text(request.url.query or "")
    LOG.info("%s %s status=%s %.0fms%s", request.method, request.url.path, response.status_code,
             elapsed, f" q={q[:160]}" if q else "")
    return response

db.init_schema()
db.seed()
accounts.ensure_bootstrap_admin()
qa.ensure_snapshot(POLICY_DB)
from .qa_routes import router as qa_router
app.include_router(qa_router)
from .qa_sessions import recover as recover_qa_turns
recover_qa_turns()


# ---------- 身份依赖 ----------
def student(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    return auth.require(x_role, x_user, {"student"}, request)


def any_user(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    return auth.current_user(x_role, x_user, request)


def staff(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    return auth.require(x_role, x_user, {"counselor", "admin", "auditor"}, request)


def reviewer(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    return auth.require(x_role, x_user, {"counselor", "admin"}, request)


def admin(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    return auth.require(x_role, x_user, {"admin"}, request)


def audit_reader(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    return auth.require(x_role, x_user, {"admin", "auditor"}, request)


# ---------- 任务存储 ----------
def save_task(task_id: str, result: dict, student_data: dict, owner: str, consent: bool) -> None:
    safe = {k: v for k, v in student_data.items() if k not in ("name", "note", "consent")}
    c = db.conn()
    c.execute(
        "REPLACE INTO triage_task(task_id,created_at,owner,policy_version,policy_verified,"
        "student_json,result_json,status,consent) VALUES(?,?,?,?,?,?,?,?,?)",
        (task_id, db.now(), owner, result["policy_version"], int(result["policy_verified"]),
         json.dumps(safe, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "triaged",
         int(consent)),
    )
    c.commit()
    c.close()


def task_row(task_id: str) -> sqlite3.Row:
    c = db.conn()
    row = c.execute("SELECT * FROM triage_task WHERE task_id=?", (task_id,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return row


def owned_or_staff(task_id: str, u: dict) -> sqlite3.Row:
    row = task_row(task_id)
    if u["role"] in ("counselor", "admin", "auditor"):
        return row
    if (row["owner"] or "student_demo") != u["user"]:
        db.audit(u["user"], u["role"], "denied", f"task:{task_id}", {"reason": "not owner"})
        raise HTTPException(status_code=403, detail="只能查看本人任务")
    return row


def events_of(task_id: str) -> list[dict]:
    c = db.conn()
    rows = c.execute("SELECT * FROM review_event WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/index.html", response_class=HTMLResponse)
def index_html():
    return index()


@app.get("/triage.html", response_class=HTMLResponse)
def triage_page():
    return (STATIC / "triage.html").read_text(encoding="utf-8")


@app.get("/qa.html", response_class=HTMLResponse)
def qa_page():
    return (STATIC / "qa.html").read_text(encoding="utf-8")


@app.get("/progress.html", response_class=HTMLResponse)
def progress_page():
    return (STATIC / "progress.html").read_text(encoding="utf-8")


@app.get("/review.html", response_class=HTMLResponse)
def review_page():
    return (STATIC / "review.html").read_text(encoding="utf-8")


@app.get("/admin.html", response_class=HTMLResponse)
def admin_page():
    return (STATIC / "admin.html").read_text(encoding="utf-8")


@app.get("/login.html", response_class=HTMLResponse)
def login_page():
    return (STATIC / "login.html").read_text(encoding="utf-8")


# ---------- 公共数据 ----------
@app.get("/api/whoami")
def whoami(u: dict = Depends(any_user)):
    return u


@app.get("/api/fields")
def fields():
    payload = json.loads(FIELDS_PATH.read_text(encoding="utf-8"))
    payload["fields"] = [f for f in payload["fields"] if f["code"] not in FORM_EXCLUDE]
    return payload


@app.get("/api/samples")
def samples():
    return json.loads((ROOT / "data" / "samples" / "students.json").read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    c = db.conn()
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("triage_task", "review_event", "appeal", "audit_log", "policy_version")}
    c.close()
    atoms, stats = tg.load_atoms()
    pc_c = policy_conn()
    total, verified = pc_c.execute(
        "SELECT COUNT(*), SUM(status IN ('verified','published')) FROM policy_staging").fetchone()
    pc_c.close()
    return {
        "ok": True, **counts,
        "policy_version": current_version_id() or tg.load_projects()["policy_version"],
        "policy_verified": bool(total) and verified == total,
        "policy_docs_verified": verified or 0,
        "policy_docs_total": total,
        "atoms_current": stats["current"],
        "atoms_superseded_excluded": stats["excluded_superseded"],
        "llm_mode": provider.mode(),
        "provider_configured": provider.configured(),
        "auth_mode": identity.mode(),
        "prompt_version": provider.PROMPT_VERSION,
    }


def current_version_id() -> str | None:
    c = db.conn()
    row = c.execute("SELECT version_id FROM policy_version WHERE status='current'"
                    " ORDER BY created_at DESC LIMIT 1").fetchone()
    c.close()
    return row["version_id"] if row else None


# ---------- B1 初判 ----------
@app.post("/api/triage")
async def create_triage(payload: Any = Body(default=None), u: dict = Depends(student)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体须为对象")
    if not payload.get("consent"):
        raise HTTPException(status_code=400, detail="需先阅读并勾选隐私授权")
    data = {k: v for k, v in payload.items() if k != "consent"}
    result = agents.run_triage(data, tg, POLICY_DB, DISCLAIMER, current_version_id())
    if result.get("error"):
        db.audit(u["user"], u["role"], "triage_invalid", "payload", {"error": result["error"]})
        raise HTTPException(status_code=422, detail=result["error"])
    result.get("student", {}).pop("name", None)
    version = current_version_id()
    if version:
        result["policy_version"] = version
    save_task(result["task_id"], result, data, u["user"], True)
    db.audit(u["user"], u["role"], "triage", result["task_id"],
             {"hit_count": result["hit_count"], "version": result["policy_version"],
              "agents": result.get("agents", {}).get("count")})
    return result


@app.get("/api/triage/{task_id}")
def get_triage(task_id: str, u: dict = Depends(any_user)):
    row = owned_or_staff(task_id, u)
    result = json.loads(row["result_json"])
    result["status"] = row["status"]
    result["created_at"] = row["created_at"]
    result["events"] = events_of(task_id)
    return result


@app.post("/api/triage/{task_id}/submit")
def submit_review(task_id: str, u: dict = Depends(student)):
    row = owned_or_staff(task_id, u)
    if row["status"] != "triaged":
        raise HTTPException(status_code=400, detail=f"当前状态 {row['status']} 不能提交复核")
    c = db.conn()
    c.execute("UPDATE triage_task SET status='reviewing' WHERE task_id=?", (task_id,))
    c.commit()
    c.execute("INSERT INTO review_event(task_id,project_id,action,actor,role,reason,created_at)"
              " VALUES(?,?,?,?,?,?,?)", (task_id, None, "submit", u["user"], u["role"], "", db.now()))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "submit_review", task_id)
    return {"task_id": task_id, "status": "reviewing"}


# ---------- C2 复核 ----------
@app.post("/api/triage/{task_id}/review")
def review(task_id: str, payload: Any = Body(default=None), u: dict = Depends(reviewer)):
    row = task_row(task_id)
    if row["status"] != "reviewing":
        raise HTTPException(status_code=400, detail=f"当前状态 {row['status']} 不在待复核")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体须为对象")
    action = payload.get("action")
    if action not in ("approve", "return"):
        raise HTTPException(status_code=422, detail="action 只能是 approve／return")
    reason = (payload.get("reason") or "").strip()
    if action == "return" and not reason:
        raise HTTPException(status_code=422, detail="退回必须填写理由")
    status = "approved" if action == "approve" else "returned"
    c = db.conn()
    c.execute("UPDATE triage_task SET status=? WHERE task_id=?", (status, task_id))
    c.execute("INSERT INTO review_event(task_id,project_id,action,actor,role,reason,created_at)"
              " VALUES(?,?,?,?,?,?,?)",
              (task_id, payload.get("project_id"), action, u["user"], u["role"], reason, db.now()))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], f"review_{action}", task_id, {"reason": reason})
    return {"task_id": task_id, "status": status}


@app.get("/api/my-tasks")
def my_tasks(limit: int = Query(20, ge=1, le=50), u: dict = Depends(student)):
    """学生本人的最近初判任务，用于“办理进度”和学生端首页。"""
    c = db.conn()
    rows = c.execute(
        "SELECT task_id,created_at,status,policy_version,result_json FROM triage_task"
        " WHERE COALESCE(owner,'student_demo')=? ORDER BY created_at DESC LIMIT ?",
        (u["user"], limit)).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["result_json"])
        except Exception:
            payload = {}
        items = payload.get("items") or []
        out.append({
            "task_id": r["task_id"], "created_at": r["created_at"], "status": r["status"],
            "policy_version": r["policy_version"],
            "hit_count": payload.get("hit_count", 0),
            "summary": [{"project_id": i.get("project_id"), "project_name": i.get("project_name"),
                         "conclusion": i.get("conclusion"), "conclusion_label": i.get("conclusion_label")}
                        for i in items if i.get("conclusion")],
        })
    c.close()
    return {"items": out, "count": len(out)}


@app.get("/api/tasks")
def tasks(status: str = Query("reviewing"), u: dict = Depends(staff)):
    c = db.conn()
    rows = c.execute(
        "SELECT task_id,created_at,status,policy_version,hit_count FROM ("
        " SELECT task_id,created_at,status,policy_version,"
        " json_extract(result_json,'$.hit_count') AS hit_count,result_json"
        " FROM triage_task ORDER BY created_at DESC) WHERE status=? LIMIT 50",
        (status,)).fetchall()
    out = []
    for r in rows:
        full = c.execute("SELECT result_json FROM triage_task WHERE task_id=?", (r["task_id"],)).fetchone()
        items = json.loads(full[0])["items"]
        out.append({
            "task_id": r["task_id"], "created_at": r["created_at"], "status": r["status"],
            "policy_version": r["policy_version"], "hit_count": r["hit_count"],
            "summary": [{"project_id": i["project_id"], "project_name": i["project_name"],
                         "conclusion": i.get("conclusion"), "conclusion_label": i.get("conclusion_label")}
                        for i in items if i.get("conclusion")],
        })
    c.close()
    return {"items": out, "count": len(out)}


@app.get("/api/board")
def board(u: dict = Depends(staff)):
    c = db.conn()
    by_status: dict[str, int] = {}
    rows = c.execute("SELECT task_id,status FROM triage_task").fetchall()
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    labels: dict[str, int] = {}
    for (raw,) in c.execute("SELECT result_json FROM triage_task").fetchall():
        for item in json.loads(raw)["items"]:
            lab = item.get("conclusion_label")
            if lab:
                labels[lab] = labels.get(lab, 0) + 1
    appeals_open = c.execute("SELECT COUNT(*) FROM appeal WHERE status='open'").fetchone()[0]
    c.close()
    total = sum(by_status.values())
    returned = by_status.get("returned", 0)
    return {
        "tasks": total, "by_status": by_status, "conclusions": labels,
        "db_path": str(db.APP_DB), "return_rate": round(returned / total, 3) if total else 0,
        "appeals_open": appeals_open,
    }


# ---------- B8 申诉 ----------
@app.post("/api/triage/{task_id}/appeal")
def appeal(task_id: str, payload: Any = Body(default=None), u: dict = Depends(student)):
    row = owned_or_staff(task_id, u)
    if row["status"] not in ("approved", "returned", "amended"):
        raise HTTPException(status_code=400, detail=f"当前状态 {row['status']} 不能申诉")
    reason = (payload or {}).get("reason", "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="申诉须填写理由")
    c = db.conn()
    c.execute("INSERT INTO appeal(task_id,project_id,reason,status,created_at) VALUES(?,?,?,?,?)",
              (task_id, (payload or {}).get("project_id"), reason, "open", db.now()))
    appeal_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("UPDATE triage_task SET status='appealed' WHERE task_id=?", (task_id,))
    c.execute("INSERT INTO review_event(task_id,project_id,action,actor,role,reason,created_at)"
              " VALUES(?,?,?,?,?,?,?)",
              (task_id, (payload or {}).get("project_id"), "appeal", u["user"], u["role"], reason, db.now()))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "appeal", task_id, {"reason": reason})
    return {"appeal_id": appeal_id, "status": "appealed"}


@app.get("/api/appeals")
def appeals(status: str = Query("open"), u: dict = Depends(audit_reader)):
    c = db.conn()
    rows = c.execute("SELECT * FROM appeal WHERE status=? ORDER BY id DESC LIMIT 50", (status,)).fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/appeal/{appeal_id}/handle")
def handle_appeal(appeal_id: int, payload: Any = Body(default=None), u: dict = Depends(admin)):
    decision = (payload or {}).get("decision")
    reason = ((payload or {}).get("reason") or "").strip()
    if decision not in ("uphold", "amend"):
        raise HTTPException(status_code=422, detail="decision 只能是 uphold／amend")
    if not reason:
        raise HTTPException(status_code=422, detail="裁决须填写理由")
    c = db.conn()
    row = c.execute("SELECT * FROM appeal WHERE id=?", (appeal_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(status_code=404, detail="申诉不存在")
    task_id = row["task_id"]
    status = "approved"
    if decision == "amend":
        new_label = (payload or {}).get("new_conclusion")
        if new_label not in ("可申请", "需核实", "暂不适用"):
            c.close()
            raise HTTPException(status_code=422, detail="改判须给 new_conclusion（可申请／需核实／暂不适用）")
        trow = c.execute("SELECT result_json FROM triage_task WHERE task_id=?", (task_id,)).fetchone()
        result = json.loads(trow["result_json"])
        for item in result["items"]:
            if item["project_id"] == row["project_id"]:
                item["conclusion"] = {"可申请": "eligible", "需核实": "need_check",
                                      "暂不适用": "not_applicable"}[new_label]
                item["conclusion_label"] = new_label
                item["notes"] = (item.get("notes") or []) + [f"甲方复核改判：{reason}"]
        result["hit_count"] = sum(1 for i in result["items"] if i.get("conclusion") in ("eligible", "need_check"))
        c.execute("UPDATE triage_task SET result_json=?,status='amended' WHERE task_id=?",
                  (json.dumps(result, ensure_ascii=False), task_id))
        status = "amended"
    c.execute("UPDATE appeal SET status='closed',handler=?,handled_at=?,decision=?,decision_reason=?"
              " WHERE id=?", (u["user"], db.now(), decision, reason, appeal_id))
    c.execute("UPDATE triage_task SET status=? WHERE task_id=?", (status, task_id))
    c.execute("INSERT INTO review_event(task_id,project_id,action,actor,role,reason,created_at)"
              " VALUES(?,?,?,?,?,?,?)",
              (task_id, row["project_id"], f"appeal_{decision}", u["user"], u["role"], reason, db.now()))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], f"appeal_handle_{decision}", task_id, {"reason": reason})
    return {"appeal_id": appeal_id, "task_id": task_id, "status": status}


# ---------- A2/A6 政策核文、发布、回滚 ----------
def policy_conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(POLICY_DB))
    c.row_factory = sqlite3.Row
    return c


@app.get("/api/policy/docs")
def policy_docs(status: str = Query(""), u: dict = Depends(audit_reader)):
    pc_c = policy_conn()
    sql = "SELECT id,doc_no,title,issuer,publish_date,effective_from,status,url,length(body_text) AS chars FROM policy_staging"
    rows = pc_c.execute(sql + (" WHERE status=?" if status else "") + " ORDER BY id",
                        ((status,) if status else ())).fetchall()
    pc_c.close()
    c = db.conn()
    ver = {r["doc_id"]: dict(r) for r in c.execute("SELECT * FROM policy_verify").fetchall()}
    c.close()
    return {"items": [{**dict(r), "verify": ver.get(r["id"])} for r in rows], "count": len(rows)}


@app.post("/api/policy/upload")
def policy_upload(payload: Any = Body(default=None), u: dict = Depends(admin)):
    data = payload or {}
    return ingest.save_upload(POLICY_DB, data.get("filename") or "", data.get("content_b64") or "", u["user"])


@app.post("/api/policy/{doc_id}/split")
def policy_split(doc_id: int, u: dict = Depends(admin)):
    c = policy_conn()
    row = c.execute("SELECT id,status FROM policy_staging WHERE id=?", (doc_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(status_code=404, detail="政策不存在")
    if row["status"] not in ("verified", "published"):
        c.close()
        raise HTTPException(status_code=400, detail="须先核文再切块（I-7）")
    c.close()
    stats = pc.split_docs([doc_id], db_path=POLICY_DB)
    if stats["clauses"] == 0:
        raise HTTPException(status_code=422, detail="未识别到条款，请检查正文分段（需含“第X条”结构）")
    c = policy_conn()
    c.execute("UPDATE policy_clause SET status='verified',publishable=1 WHERE doc_id=?", (doc_id,))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "policy_split", f"doc:{doc_id}", stats)
    return {"doc_id": doc_id, **stats, "published": False,
            "next": "到“版本与回滚”点发布，学生端才会按新版本检索"}


@app.post("/api/policy/{doc_id}/verify")
def verify_doc(doc_id: int, payload: Any = Body(default=None), u: dict = Depends(admin)):
    data = payload or {}
    doc_no = (data.get("doc_no") or "").strip()
    issuer = (data.get("issuer") or "").strip()
    effective_from = (data.get("effective_from") or "").strip()
    if not doc_no or not issuer:
        raise HTTPException(status_code=422, detail="文号与发布机关必填（I-7）")
    pc_c = policy_conn()
    row = pc_c.execute("SELECT id FROM policy_staging WHERE id=?", (doc_id,)).fetchone()
    if not row:
        pc_c.close()
        raise HTTPException(status_code=404, detail="政策不存在")
    pc_c.execute("UPDATE policy_staging SET status='verified', doc_no=?, issuer=?, effective_from=?"
                 " WHERE id=?", (doc_no, issuer, effective_from or None, doc_id))
    pc_c.execute("UPDATE policy_clause SET status='verified',publishable=1 WHERE doc_id=?", (doc_id,))
    pc_c.commit()
    pc_c.close()
    c = db.conn()
    c.execute("INSERT OR REPLACE INTO policy_verify(doc_id,doc_no,issuer,effective_from,verifier,"
              "verified_at,note) VALUES(?,?,?,?,?,?,?)",
              (doc_id, doc_no, issuer, effective_from, data.get("verifier") or u["user"], db.now(),
               data.get("note", "")))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "policy_verify", f"doc:{doc_id}", {"doc_no": doc_no})
    return {"doc_id": doc_id, "status": "verified", "doc_no": doc_no}


@app.post("/api/policy/{doc_id}/publish")
def publish(doc_id: int, u: dict = Depends(admin)):
    pc_c = policy_conn()
    doc = pc_c.execute("SELECT id,doc_no,title,status FROM policy_staging WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        pc_c.close()
        raise HTTPException(status_code=404, detail="政策不存在")
    if doc["status"] != "verified":
        pc_c.close()
        raise HTTPException(status_code=400, detail="未核文的政策不得发布（I-7）")
    if pc_c.execute("SELECT COUNT(*) FROM policy_clause WHERE doc_id=?", (doc_id,)).fetchone()[0] == 0:
        pc_c.close()
        raise HTTPException(status_code=400, detail="该政策尚未切块，请先“切块”再发布")
    docs = [dict(r) for r in pc_c.execute(
        "SELECT id,doc_no,title,status FROM policy_staging WHERE status IN ('verified','published')").fetchall()]
    doc_ids = [d["id"] for d in docs]
    clauses = 0
    if doc_ids:
        marks = ",".join("?" for _ in doc_ids)
        clauses = pc_c.execute(f"SELECT COUNT(*) FROM policy_clause WHERE doc_id IN ({marks})",
                               doc_ids).fetchone()[0]
    pc_c.close()
    atoms, stats = tg.load_atoms()
    version_id = "pv-" + db.now().replace(":", "").replace("-", "")[:15]
    snapshot = {"docs": docs, "clauses": clauses, "atoms_current": stats["current"],
                "atoms_superseded": stats["excluded_superseded"]}
    c = db.conn()
    old = c.execute("SELECT version_id FROM policy_version WHERE status='current'").fetchall()
    for (vid,) in old:
        c.execute("UPDATE policy_version SET status='archived' WHERE version_id=?", (vid,))
    c.execute("INSERT INTO policy_version(version_id,created_at,published_by,label,docs,clauses,atoms,"
              "status,snapshot_json) VALUES(?,?,?,?,?,?,?,?,?)",
              (version_id, db.now(), u["user"], version_id, len(docs), clauses, stats["current"], "current",
               json.dumps(snapshot, ensure_ascii=False)))
    c.commit()
    c.close()
    snap = qa.write_snapshot(version_id, POLICY_DB)
    db.audit(u["user"], u["role"], "policy_publish", f"doc:{doc_id}",
             {"version": version_id, "snapshot_clauses": snap["clauses"], "snapshot_atoms": snap["atoms"]})
    return {"version_id": version_id, "docs": len(docs), "clauses": clauses, "atoms": stats["current"],
            "snapshot": snap}


@app.get("/api/policy/versions")
def versions(u: dict = Depends(audit_reader)):
    c = db.conn()
    rows = c.execute("SELECT version_id,created_at,published_by,docs,clauses,atoms,status"
                     " FROM policy_version ORDER BY created_at DESC").fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/policy/rollback")
def rollback(payload: Any = Body(default=None), u: dict = Depends(admin)):
    version_id = (payload or {}).get("version_id")
    c = db.conn()
    target = c.execute("SELECT * FROM policy_version WHERE version_id=?", (version_id,)).fetchone()
    if not target:
        c.close()
        raise HTTPException(status_code=404, detail="版本不存在")
    if not qa.has_snapshot(version_id, POLICY_DB):
        c.close()
        raise HTTPException(status_code=409, detail="该版本没有条款快照，回滚将无法恢复内容，已阻止（请重新发布该版本）")
    for row in c.execute("SELECT version_id FROM policy_version WHERE status='current'").fetchall():
        c.execute("UPDATE policy_version SET status='rolled_back' WHERE version_id=?", (row["version_id"],))
    c.execute("UPDATE policy_version SET status='current' WHERE version_id=?", (version_id,))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "policy_rollback", version_id)
    return {"current": version_id, "status": "current"}


# ---------- A7 检索 / B5 问答 ----------
def search_clauses(q: str, limit: int = 5) -> list[dict]:
    try:
        return qa.search_clauses(q, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/policy/search")
def policy_search(q: str = Query(...), limit: int = Query(5, ge=1, le=20), u: dict = Depends(any_user)):
    hits = search_clauses(q, limit)
    db.audit(u["user"], u["role"], "policy_search", q[:40], {"hits": len(hits)})
    return {"query": q, "hits": hits, "count": len(hits)}


@app.post("/api/qa")
def qa_answer(payload: Any = Body(default=None), u: dict = Depends(any_user)):
    question = ((payload or {}).get("question") or "").strip()
    if len(question) < 2:
        raise HTTPException(status_code=422, detail="问题至少 2 个字")
    result = qa.answer(question, db_path=POLICY_DB)
    if result["refused"]:
        db.audit(u["user"], u["role"], "qa_refused", "legacy", {"version": result.get("version_id")})
    else:
        db.audit(u["user"], u["role"], "qa", "legacy",
                 {"hits": len(result["hits"]), "mode": result["mode"]})
    return result


@app.get("/api/provider")
def provider_status(u: dict = Depends(any_user)):
    return provider.status()


@app.get("/api/auth/whoami")
def whoami(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    token = request.cookies.get("fund_session")
    su = accounts.session_user(token) if token else None
    if su:
        return {**su, "mode": identity.mode(), "cas_configured": bool(identity.cas_base())}
    return identity.whoami(x_role, x_user)


# ---------- G1 注册、登录、审核与改密 ----------
@app.post("/api/auth/register")
def auth_register(payload: Any = Body(default=None)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体须为对象")
    return accounts.register(payload)


@app.post("/api/auth/login")
def auth_login(request: Request, payload: Any = Body(default=None)):
    data = payload or {}
    out = accounts.login((data.get("nickname") or "").strip(), data.get("password") or "")
    resp = JSONResponse({"ok": True, "role": out["role"], "user": out["user"],
                         "nickname": out["nickname"], "must_change": out["must_change"],
                         "expires_at": out["expires_at"],
                         "guidance": "登录成功。忘记密码请联系管理员重置，临时密码只显示一次。"})
    resp.set_cookie("fund_session", out["token"], max_age=accounts.SESSION_HOURS * 3600,
                    httponly=True, samesite="lax", path=_cookie_path(request))
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = request.cookies.get("fund_session")
    accounts.logout(token, (accounts.session_user(token) or {}).get("user", "") if token else "")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("fund_session", path=_cookie_path(request))
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request, x_role: str = Header("student"), x_user: str = Header("student_demo")):
    token = request.cookies.get("fund_session")
    su = accounts.session_user(token) if token else None
    if su:
        return {**su, "logged_in": True}
    u = auth.current_user(x_role, x_user, request)
    return {**u, "logged_in": False,
            "note": "当前为演示态身份（请求头）。注册并登录后按账号角色授权。"}


@app.post("/api/auth/change-password")
def auth_change_password(payload: Any = Body(default=None), u: dict = Depends(any_user)):
    data = payload or {}
    if u.get("source") != "session":
        raise HTTPException(status_code=400, detail="演示态身份不支持改密，请先登录")
    return accounts.change_password(u, data.get("old_password") or "", data.get("new_password") or "")


@app.get("/api/admin/registrations")
def admin_registrations(status: str = Query(""), u: dict = Depends(admin)):
    return accounts.list_registrations(status)


@app.post("/api/admin/registrations/{user_id}/review")
def admin_review_registration(user_id: str, payload: Any = Body(default=None),
                              u: dict = Depends(admin)):
    data = payload or {}
    return accounts.review_registration(user_id, (data.get("action") or "").strip(), u["user"],
                                        (data.get("role") or "student").strip(),
                                        (data.get("note") or "").strip())


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, u: dict = Depends(admin)):
    return accounts.reset_password(user_id, u["user"])



# ---------- B3 材料 / D4 分档 ----------
@app.get("/api/materials")
def materials(project_id: str = Query(""), u: dict = Depends(any_user)):
    c = db.conn()
    rows = c.execute("SELECT * FROM material_item" + (" WHERE project_id=?" if project_id else ""),
                     ((project_id,) if project_id else ())).fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.get("/api/school-tiers")
def tiers(u: dict = Depends(any_user)):
    c = db.conn()
    rows = c.execute("SELECT * FROM school_tier ORDER BY id").fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows),
            "note": "未配置校级细则时，档位结论恒为需核实"}


@app.post("/api/school-tiers")
def add_tier(payload: Any = Body(default=None), u: dict = Depends(admin)):
    data = payload or {}
    level = (data.get("level") or "").strip()
    if not level:
        raise HTTPException(status_code=422, detail="档位名称必填")
    c = db.conn()
    c.execute("INSERT INTO school_tier(level,income_band,amount,note) VALUES(?,?,?,?)",
              (level, data.get("income_band", ""), data.get("amount"), data.get("note", "")))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "school_tier_add", level)
    return {"added": level}


@app.get("/api/audit")
def audit_log(limit: int = Query(50, ge=1, le=500), u: dict = Depends(audit_reader)):
    c = db.conn()
    rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


# ---------- 原文与导出 ----------
@app.get("/api/clause/{clause_id}")
def get_clause(clause_id: int, version_id: str | None = Query(None), u: dict = Depends(any_user)):
    if version_id:
        from .qa_evidence import resolve
        try:
            item = resolve(version_id, clause_id, POLICY_DB)
        except LookupError:
            raise HTTPException(404, "该版本条款不存在") from None
        return {**item, "text": item["quote"], "status": "published_snapshot"}
    c = policy_conn()
    row = c.execute(
        "SELECT c.id,c.clause_no,c.text,c.char_start,c.char_end,p.doc_no,p.title,p.url,"
        "p.effective_from,p.status FROM policy_clause c JOIN policy_staging p ON p.id=c.doc_id"
        " WHERE c.id=?", (clause_id,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(status_code=404, detail="条款不存在")
    return {"clause_id": row["id"], "article_no": row["clause_no"] or "", "text": row["text"],
            "char_start": row["char_start"], "char_end": row["char_end"], "doc_no": row["doc_no"],
            "policy_title": row["title"], "url": row["url"],
            "effective_from": row["effective_from"], "status": row["status"]}


def build_export(result: dict, status: str) -> str:
    lines = ["# 资助政策初判结果", "",
             f"- 任务号：{result['task_id']}", f"- 任务状态：{status}",
             f"- 政策版本：{result['policy_version']}（policy_verified=false）",
             f"- 说明模式：{'离线规则模式' if provider.mode() != 'online' else '在线模型模式'}"
             f"（提示词 {provider.PROMPT_VERSION}；判等与引用由程序完成）",
             f"- 生成时间：{db.now()}", "", f"> {result['disclaimer']}", "",
             "## 结论", "", "| 项目 | 结论 | 缺项 | 需评审 | 依据条款 |", "|---|---|---|---|---|"]
    for item in result["items"]:
        if not item.get("conclusion"):
            continue
        refs = "；".join(f"{r['doc_no']} {r['article_no']}" for r in item.get("reasons", []))
        lines.append(f"| {item['project_name']} | {item['conclusion_label']} | "
                     f"{'、'.join(item.get('missing_fields') or []) or '—'} | "
                     f"{'是' if item.get('review_required') else '否'} | {refs or '—'} |")
    if not result.get("hit_count"):
        lines += ["", result.get("fallback", "")]
    lines += ["", "## 依据原文", ""]
    for item in result["items"]:
        for r in item.get("reasons", []):
            lines.append(f"- **{r['doc_no']} {r['article_no']}**：{r['quote']}")
            lines.append(f"  - 正文 {r['char_start']}–{r['char_end']}，条款 ID {r['clause_id']}")
    lines += ["", "## 材料清单（来源见括注）", ""]
    c = db.conn()
    for item in result["items"]:
        if item.get("conclusion") not in ("eligible", "need_check"):
            continue
        lines.append(f"**{item['project_name']}**")
        rows = c.execute("SELECT name,source_type FROM material_item WHERE project_id=?",
                         (item["project_id"],)).fetchall()
        for r in rows:
            lines.append(f"- [ ] {r['name']}（来源：{r['source_type']}）")
        lines.append(f"- 办理渠道：{item.get('channel', '')}")
        lines.append("")
    c.close()
    lines += ["## 说明", "",
              "- 本结果由确定性规则生成，不含模型判定；金额仅作展示，不参与判等。",
              "- 政策文号与生效日期尚未全部人工核对，本件仅供内部验证。", ""]
    return "\n".join(lines)


@app.get("/api/export/{task_id}")
def export(task_id: str, u: dict = Depends(any_user)):
    row = owned_or_staff(task_id, u)
    result = json.loads(row["result_json"])
    md = build_export(result, row["status"])
    c = db.conn()
    approval = c.execute("SELECT reviewer,reviewed_at,review_note FROM export_request"
                         " WHERE task_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
                         (task_id,)).fetchone()
    c.close()
    if approval:
        md += (f"\n导出审批：已批准（审批人 {approval['reviewer']}，{approval['reviewed_at']}）"
               f"{'；备注 ' + approval['review_note'] if approval['review_note'] else ''}\n")
    db.audit(u["user"], u["role"], "export", task_id)
    return Response(content=md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="triage-{task_id}.md"'})


# ---------- C3 批量初筛 / C5 院校两级看板 / C6 预警 ----------
def hours_since(iso: str) -> float:
    try:
        return round((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600, 1)
    except Exception:
        return 0.0


def project_names() -> dict[str, str]:
    return {p["code"]: p.get("name", p["code"]) for p in tg.load_projects()["projects"]}


@app.get("/api/review/queue")
def review_queue(status: str = Query("reviewing"), limit: int = Query(50, ge=1, le=200),
                 overdue_hours: float = Query(72, ge=1), u: dict = Depends(staff)):
    """批量初筛队列：一次取多条待复核任务，带等待时长与风险标记。"""
    c = db.conn()
    rows = c.execute("SELECT task_id,created_at,status,owner,policy_version,result_json FROM triage_task"
                     " WHERE status=? ORDER BY created_at LIMIT ?", (status, limit)).fetchall()
    out = []
    for r in rows:
        result = json.loads(r["result_json"])
        items = [i for i in result["items"] if i.get("conclusion")]
        missing = sorted({f for i in items for f in (i.get("missing_fields") or [])})
        wait = hours_since(r["created_at"])
        out.append({
            "task_id": r["task_id"], "created_at": r["created_at"], "status": r["status"],
            "owner": r["owner"], "policy_version": r["policy_version"],
            "hit_count": result.get("hit_count"), "wait_hours": wait,
            "overdue": wait >= overdue_hours,
            "review_required": any(i.get("review_required") for i in items),
            "missing_fields": missing,
            "summary": [{"project_id": i["project_id"], "project_name": i["project_name"],
                         "conclusion": i.get("conclusion"), "conclusion_label": i.get("conclusion_label")}
                        for i in items],
        })
    c.close()
    return {"items": out, "count": len(out), "status": status, "overdue_hours": overdue_hours}


@app.post("/api/review/batch")
def review_batch(payload: Any = Body(default=None), u: dict = Depends(reviewer)):
    data = payload or {}
    task_ids = data.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        raise HTTPException(status_code=422, detail="task_ids 须为非空数组")
    if len(task_ids) > 100:
        raise HTTPException(status_code=422, detail="单批最多 100 条")
    action = data.get("action")
    if action not in ("approve", "return"):
        raise HTTPException(status_code=422, detail="action 只能是 approve／return")
    reason = (data.get("reason") or "").strip()
    if action == "return" and not reason:
        raise HTTPException(status_code=422, detail="批量退回必须填写理由")
    new_status = "approved" if action == "approve" else "returned"
    ok, skipped = [], []
    c = db.conn()
    for tid in task_ids:
        row = c.execute("SELECT status,result_json FROM triage_task WHERE task_id=?", (tid,)).fetchone()
        if not row:
            skipped.append({"task_id": tid, "why": "任务不存在"})
            continue
        if row["status"] != "reviewing":
            skipped.append({"task_id": tid, "why": f"状态 {row['status']} 不在待复核"})
            continue
        c.execute("UPDATE triage_task SET status=? WHERE task_id=?", (new_status, tid))
        c.execute("INSERT INTO review_event(task_id,project_id,action,actor,role,reason,created_at)"
                  " VALUES(?,?,?,?,?,?,?)", (tid, None, f"batch_{action}", u["user"], u["role"], reason, db.now()))
        ok.append(tid)
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "review_batch", f"{action}:{len(ok)}",
             {"reason": reason, "skipped": len(skipped)})
    return {"action": action, "status": new_status, "ok": ok, "skipped": skipped,
            "count_ok": len(ok), "count_skipped": len(skipped)}


@app.get("/api/board/levels")
def board_levels(overdue_hours: float = Query(72, ge=1), u: dict = Depends(staff)):
    """两级看板：校级总览（状态／退回率／申诉）＋ 项目级结论分布（可申请／需核实／暂不适用）。"""
    c = db.conn()
    tasks = c.execute("SELECT task_id,created_at,status,result_json FROM triage_task").fetchall()
    c.close()
    names = project_names()
    by_status: dict[str, int] = {}
    projects: dict[str, dict] = {code: {"project_id": code, "project_name": name, "eligible": 0,
                                        "need_check": 0, "not_applicable": 0, "tasks": 0}
                                 for code, name in names.items()}
    overdue = 0
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        if t["status"] == "reviewing" and hours_since(t["created_at"]) >= overdue_hours:
            overdue += 1
        for item in json.loads(t["result_json"])["items"]:
            code = item.get("project_id")
            if code not in projects:
                continue
            projects[code]["tasks"] += 1
            if item.get("conclusion") in ("eligible", "need_check", "not_applicable"):
                projects[code][item["conclusion"]] += 1
    total = sum(by_status.values())
    returned = by_status.get("returned", 0)
    return {
        "school": {"tasks": total, "by_status": by_status,
                   "return_rate": round(returned / total, 3) if total else 0,
                   "overdue": overdue, "overdue_hours": overdue_hours,
                   "pending_review": by_status.get("reviewing", 0)},
        "projects": sorted(projects.values(), key=lambda p: p["project_id"]),
        "db_path": str(db.APP_DB),
    }


@app.get("/api/warnings")
def warnings(overdue_hours: float = Query(72, ge=1), u: dict = Depends(staff)):
    """预警：待复核超时、退回率偏高、未核文政策、版本过期、待处理申诉、待审批导出。"""
    c = db.conn()
    rows = c.execute("SELECT created_at,status FROM triage_task").fetchall()
    appeals_open = c.execute("SELECT COUNT(*) FROM appeal WHERE status='open'").fetchone()[0]
    exp_pending = c.execute("SELECT COUNT(*) FROM export_request WHERE status='pending'").fetchone()[0]
    ver = c.execute("SELECT version_id,created_at FROM policy_version WHERE status='current'"
                    " ORDER BY created_at DESC LIMIT 1").fetchone()
    c.close()
    total = len(rows)
    overdue = sum(1 for r in rows if r["status"] == "reviewing" and hours_since(r["created_at"]) >= overdue_hours)
    returned = sum(1 for r in rows if r["status"] == "returned")
    pc_c = policy_conn()
    docs_total, docs_verified = pc_c.execute(
        "SELECT COUNT(*), SUM(status IN ('verified','published')) FROM policy_staging").fetchone()
    pc_c.close()
    out = []
    if overdue:
        out.append({"code": "review_overdue", "level": "warn", "count": overdue,
                    "title": f"{int(overdue_hours)} 小时以上未复核", "detail": "存在长期挂起的待复核任务"})
    if total and returned / total >= 0.3:
        out.append({"code": "return_rate_high", "level": "warn", "count": returned,
                    "title": "退回率偏高", "detail": f"退回率 {returned}/{total}，建议抽查初判口径"})
    if docs_verified < docs_total:
        out.append({"code": "policy_unverified", "level": "info", "count": docs_total - (docs_verified or 0),
                    "title": "政策未全部核文", "detail": f"{docs_verified or 0}/{docs_total} 份已核，未核文不得发布"})
    if not ver:
        out.append({"code": "no_version", "level": "warn", "count": 1,
                    "title": "尚无发布版本", "detail": "初判结果只能锚定草稿版本"})
    elif hours_since(ver["created_at"]) > 24 * 30:
        out.append({"code": "version_stale", "level": "info",
                    "count": round(hours_since(ver["created_at"]) / 24),
                    "title": "政策版本超过 30 天未更新", "detail": f"当前版本 {ver['version_id']}"})
    if appeals_open:
        out.append({"code": "appeals_open", "level": "warn", "count": appeals_open,
                    "title": "待处理申诉", "detail": "申诉需管理员裁决并留痕"})
    if exp_pending:
        out.append({"code": "export_pending", "level": "info", "count": exp_pending,
                    "title": "待审批导出", "detail": "导出件对外报送前须管理员审批"})
    return {"items": out, "count": len(out), "current_version": ver["version_id"] if ver else None,
            "docs_verified": docs_verified or 0, "docs_total": docs_total}


@app.get("/api/report/summary")
def report_summary(days: int = Query(7, ge=1, le=365), u: dict = Depends(audit_reader)):
    """报送报表：按时间窗统计任务状态、结论分布、项目分布，附政策版本。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    c = db.conn()
    rows = c.execute("SELECT task_id,created_at,status,policy_version,result_json FROM triage_task"
                     " WHERE created_at>=?", (since,)).fetchall()
    ver = c.execute("SELECT version_id,created_at,docs,clauses,atoms FROM policy_version"
                    " WHERE status='current' ORDER BY created_at DESC LIMIT 1").fetchone()
    c.close()
    names = project_names()
    by_status: dict[str, int] = {}
    labels: dict[str, int] = {}
    projects: dict[str, dict] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        for item in json.loads(r["result_json"])["items"]:
            lab = item.get("conclusion_label")
            if not lab:
                continue
            labels[lab] = labels.get(lab, 0) + 1
            code = item.get("project_id")
            p = projects.setdefault(code, {"project_id": code, "project_name": names.get(code, code),
                                           "可申请": 0, "需核实": 0, "暂不适用": 0})
            p[lab] = p.get(lab, 0) + 1
    return {
        "window_days": days, "since": since, "generated_at": db.now(),
        "tasks": len(rows), "by_status": by_status, "conclusions": labels,
        "projects": sorted(projects.values(), key=lambda p: p["project_id"]),
        "policy_version": ver["version_id"] if ver else None,
        "policy_label": dict(ver) if ver else None,
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/report/export.csv")
def report_csv(days: int = Query(7, ge=1, le=365), u: dict = Depends(audit_reader)):
    """报送用 CSV（Excel 可直接打开；带 BOM）。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    c = db.conn()
    rows = c.execute("SELECT task_id,created_at,status,policy_version,result_json FROM triage_task"
                     " WHERE created_at>=? ORDER BY created_at", (since,)).fetchall()
    c.close()
    lines = ["任务号,创建时间,状态,政策版本,项目,结论,缺项,是否需评审"]
    for r in rows:
        for item in json.loads(r["result_json"])["items"]:
            if not item.get("conclusion"):
                continue
            cells = [r["task_id"], r["created_at"], r["status"], r["policy_version"] or "",
                     item["project_name"], item.get("conclusion_label") or "",
                     "、".join(item.get("missing_fields") or []), "是" if item.get("review_required") else "否"]
            lines.append(",".join('"' + str(x).replace('"', '""') + '"' for x in cells))
    body = "\ufeff" + "\n".join(lines) + "\n"
    db.audit(u["user"], u["role"], "report_export_csv", f"days={days}", {"rows": len(rows)})
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="fund-report.csv"'})


# ---------- D1 账号权限 / D6 导出审批 ----------
@app.get("/api/users")
def list_users(u: dict = Depends(audit_reader)):
    c = db.conn()
    rows = c.execute("SELECT user_id,nickname,name,role,enabled,status,updated_at FROM app_user ORDER BY user_id").fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/users/{user_id}/enable")
def enable_user(user_id: str, payload: Any = Body(default=None), u: dict = Depends(admin)):
    if "enabled" not in (payload or {}):
        raise HTTPException(status_code=422, detail="须给 enabled（true／false）")
    enabled = 1 if (payload or {}).get("enabled") else 0
    c = db.conn()
    row = c.execute("SELECT user_id,role FROM app_user WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(status_code=404, detail="账号不存在")
    if not enabled and row["role"] == "admin":
        ids = [r["user_id"] for r in c.execute("SELECT user_id FROM app_user WHERE role='admin'"
                                               " AND enabled=1").fetchall()]
        if ids == [user_id]:
            c.close()
            raise HTTPException(status_code=400, detail="不能停用唯一启用的管理员账号")
    c.execute("UPDATE app_user SET enabled=?, updated_at=? WHERE user_id=?", (enabled, db.now(), user_id))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "user_enable" if enabled else "user_disable", user_id)
    return {"user_id": user_id, "enabled": bool(enabled)}


@app.post("/api/export-requests")
def create_export_request(payload: Any = Body(default=None), u: dict = Depends(any_user)):
    if u["role"] not in ("student", "counselor"):
        db.audit(u["user"], u["role"], "denied", "export_request", {"reason": "role"})
        raise HTTPException(status_code=403, detail="仅学生或辅导员可发起导出审批")
    task_id = ((payload or {}).get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="task_id 必填")
    owned_or_staff(task_id, u)
    note = ((payload or {}).get("note") or "").strip()
    c = db.conn()
    c.execute("INSERT INTO export_request(task_id,requester,role,note,status,created_at)"
              " VALUES(?,?,?,?,'pending',?)", (task_id, u["user"], u["role"], note, db.now()))
    rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], "export_request", task_id, {"request_id": rid})
    return {"request_id": rid, "status": "pending"}


@app.get("/api/export-requests")
def list_export_requests(status: str = Query(""), u: dict = Depends(staff)):
    c = db.conn()
    sql = "SELECT * FROM export_request" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT 50"
    rows = c.execute(sql, ((status,) if status else ())).fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/export-requests/{request_id}/review")
def review_export_request(request_id: int, payload: Any = Body(default=None), u: dict = Depends(admin)):
    action = ((payload or {}).get("action") or "").strip()
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="action 只能是 approve／reject")
    note = ((payload or {}).get("note") or "").strip()
    if action == "reject" and not note:
        raise HTTPException(status_code=422, detail="驳回须填写理由")
    c = db.conn()
    row = c.execute("SELECT * FROM export_request WHERE id=?", (request_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(status_code=404, detail="导出申请不存在")
    if row["status"] != "pending":
        c.close()
        raise HTTPException(status_code=400, detail=f"申请状态 {row['status']} 不可再审批")
    status = "approved" if action == "approve" else "rejected"
    c.execute("UPDATE export_request SET status=?,reviewer=?,reviewed_at=?,review_note=? WHERE id=?",
              (status, u["user"], db.now(), note, request_id))
    c.commit()
    c.close()
    db.audit(u["user"], u["role"], f"export_{action}", f"request:{request_id}",
             {"task_id": row["task_id"], "note": note})
    return {"request_id": request_id, "status": status}
