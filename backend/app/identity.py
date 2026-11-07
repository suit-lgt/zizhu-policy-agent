"""统一身份占位：dev（请求头演示态）／cas（学校统一身份，未配置时明确报错）。

替换点：`backend/app/auth.py` 的 `current_user()` 只依赖本模块返回的 {role, user}；
接学校 CAS/OAuth 时，把 `resolve()` 换成票据校验即可，其余路由不动。
"""
from __future__ import annotations

import os

from fastapi import HTTPException

from . import db

ROLES = {"student", "counselor", "admin", "auditor"}
MODES = ("dev", "cas")


def mode() -> str:
    m = (os.environ.get("AUTH_MODE") or "dev").strip().lower()
    return m if m in MODES else "dev"


def cas_base() -> str:
    return (os.environ.get("CAS_BASE") or "").rstrip("/")


def whoami(role: str, user: str) -> dict:
    m = mode()
    if m == "cas":
        # 未配置或未实现票据校验时不做"假登录"，明确返回未启用
        raise HTTPException(status_code=501,
                            detail="统一身份（CAS）未配置：请设置 CAS_BASE 并实现票据校验后再启用 AUTH_MODE=cas")
    if role not in ROLES:
        db.audit(user or "unknown", role or "unknown", "denied", "role", {"reason": "unknown role"})
        raise HTTPException(status_code=403, detail="角色非法")
    if not db.user_enabled(user or "student_demo"):
        db.audit(user, role, "denied", "account", {"reason": "disabled"})
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    return {"mode": m, "role": role, "user": user, "source": "X-Role / X-User（演示态）",
            "cas_configured": bool(cas_base()),
            "note": "接学校统一身份时替换 backend/app/identity.py 的 whoami 校验，路由与权限判断不变"}
