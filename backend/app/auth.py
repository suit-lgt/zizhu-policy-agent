"""身份与权限：登录会话优先，演示态请求头兜底。

会话来自 HttpOnly Cookie `fund_session`；没有会话时按 `X-Role`／`X-User` 请求头处理
（仅演示／冒烟用）。接学校统一身份时只替换本文件的 current_user，路由的 require 判断不变。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from . import accounts, db

ROLES = {"student", "counselor", "admin", "auditor"}
STAFF = {"counselor", "admin", "auditor"}


def current_user(role: str = "student", user: str = "student_demo",
                 request: Request | None = None) -> dict:
    token = request.cookies.get("fund_session") if request is not None else None
    if token:
        su = accounts.session_user(token)
        if su:
            return su
    if role not in ROLES:
        db.audit(user or "unknown", role or "unknown", "denied", "role", {"reason": "unknown role"})
        raise HTTPException(status_code=403, detail="角色非法")
    if not db.user_enabled(user or "student_demo"):
        db.audit(user, role, "denied", "account", {"reason": "disabled"})
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    return {"role": role, "user": user, "nickname": user, "source": "header", "must_change": False}


def require(role_header: str, user_header: str, allowed: set[str],
            request: Request | None = None) -> dict:
    u = current_user(role_header, user_header, request)
    if u["role"] not in allowed:
        db.audit(u["user"], u["role"], "denied", "api", {"allowed": sorted(allowed)})
        raise HTTPException(status_code=403, detail=f"需要角色：{'/'.join(sorted(allowed))}")
    return u
