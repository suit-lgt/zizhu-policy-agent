"""账号：注册申请、审核、登录会话、密码重置与修改。

口令用标准库 PBKDF2-HMAC-SHA256（200k 轮，16 字节盐），不引第三方依赖；
手机号只存脱敏形式（138****5678），学校选填。所有写操作落审计。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import db

ROLES = {"student", "counselor", "admin", "auditor"}
ROLE_LABELS = {"student": "学生本人", "counselor": "高校教师／辅导员",
               "admin": "资助中心管理员", "auditor": "审计员"}
SESSION_HOURS = 12
NICK_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_-]{2,20}$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

GUIDE_REGISTER = [
    "第 1 步：填写昵称和密码（必填）。昵称就是登录名，2–20 位，可用中文、字母、数字、下划线；密码至少 8 位且同时含字母和数字。",
    "第 2 步：学校和手机号选填。手机号只用于管理员联系你，不填也不影响提交申请。",
    "第 3 步：提交后生成申请编号，状态为“待审核”。审核由学校资助中心管理员完成，通常 1 个工作日内。",
    "第 4 步：审核通过后用昵称＋密码登录；被驳回会显示理由，可修改后重新提交。",
]
GUIDE_PENDING = "申请已提交，等待管理员审核。审核通过后直接用昵称＋密码登录，无需再次注册；忘记密码请联系管理员重置。"
GUIDE_LOGIN = [
    "已有账号：输入注册时的昵称和密码登录。",
    "没有账号：点“注册新账号”，按 4 步填写；提交后等管理员审核。",
    "忘记密码：联系学校资助中心管理员，在“账号权限”里重置，临时密码只显示一次。",
]
GUIDE_ADMIN_REVIEW = [
    "第 1 步：核对昵称、学校与申请时间；手机号已脱敏，仅在需要联系本人时查看。",
    "第 2 步：选择角色——学生本人／高校教师（辅导员）／审计员／管理员，默认学生本人。",
    "第 3 步：点“通过”，账号立即可登录；点“驳回”必须填理由，申请人登录时会看到。",
    "第 4 步：通过后如需改密，到“账号权限”点“重置密码”，临时密码只显示一次，请线下告知本人。",
]
GUIDE_RESET = "临时密码只显示这一次，请立即线下告知本人；本人登录后建议自行修改密码。"


def _hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}"


def _verify(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, rounds, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                bytes.fromhex(salt), int(rounds))
        return algo == "pbkdf2_sha256" and hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def ensure_bootstrap_admin(nickname: str = "admin", password: str = "admin@2026") -> dict:
    """部署初始化：确保存在可登录的默认管理员（只在缺失时创建，不覆盖已改过的密码）。"""
    c = db.conn()
    row = c.execute("SELECT user_id,nickname,role,status,password_hash FROM app_user"
                    " WHERE nickname=?", (nickname,)).fetchone()
    created = False
    if not row:
        c.execute("INSERT INTO app_user(user_id,name,nickname,role,status,enabled,password_hash,"
                  "created_at,reviewed_by,reviewed_at,must_change_password)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                  ("admin", "系统管理员", nickname, "admin", "active", 1, _hash(password),
                   db.now(), "system", db.now()))
        created = True
    else:
        uid, role, status, ph = row["user_id"], row["role"], row["status"], row["password_hash"]
        if uid == "admin":
            if not ph:
                c.execute("UPDATE app_user SET password_hash=?,role='admin',status='active',"
                          "enabled=1,must_change_password=0 WHERE user_id=?",
                          (_hash(password), uid))
            else:
                c.execute("UPDATE app_user SET role='admin',status='active',enabled=1 WHERE user_id=?",
                          (uid,))
    c.commit()
    row = c.execute("SELECT user_id,nickname,role,status,enabled,password_hash IS NOT NULL AS has_pwd"
                    " FROM app_user WHERE nickname=?", (nickname,)).fetchone()
    c.close()
    return {"created": created, **dict(row)}


def mask_phone(phone: str) -> str:
    return phone[:3] + "****" + phone[-4:] if len(phone) == 11 else phone


def validate_registration(nickname: str, password: str, school: str, phone: str) -> None:
    if not NICK_RE.match(nickname or ""):
        raise HTTPException(status_code=422, detail="昵称须 2–20 位，可用中文、字母、数字、下划线或短横线")
    if len(password or "") < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=422, detail="密码至少 8 位，且同时包含字母和数字")
    if school and len(school) > 40:
        raise HTTPException(status_code=422, detail="学校名称不超过 40 字")
    if phone and not PHONE_RE.match(phone):
        raise HTTPException(status_code=422, detail="手机号格式不正确（11 位，1 开头）")


def register(payload: dict) -> dict:
    nickname = (payload.get("nickname") or "").strip()
    password = payload.get("password") or ""
    school = (payload.get("school") or "").strip()
    phone = (payload.get("phone") or "").strip()
    validate_registration(nickname, password, school, phone)
    c = db.conn()
    dup = c.execute("SELECT user_id,status FROM app_user WHERE nickname=?", (nickname,)).fetchone()
    if dup:
        c.close()
        raise HTTPException(status_code=409, detail="该昵称已被占用，请换一个")
    user_id = "u-" + secrets.token_hex(6)
    c.execute(
        "INSERT INTO app_user(user_id,name,nickname,password_hash,school,phone,role,status,enabled,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, nickname, nickname, _hash(password), school or None, mask_phone(phone) if phone else None,
         "student", "pending", 0, db.now(), db.now()),
    )
    c.commit()
    c.close()
    db.audit(user_id, "guest", "register_apply", user_id, {"school": school})
    return {"user_id": user_id, "nickname": nickname, "status": "pending",
            "guidance": GUIDE_PENDING, "steps": GUIDE_REGISTER}


def list_registrations(status: str = "") -> dict:
    c = db.conn()
    sql = ("SELECT user_id,nickname,school,phone,role,status,enabled,created_at,reviewed_by,"
           "reviewed_at,review_note FROM app_user WHERE nickname IS NOT NULL")
    if status:
        sql += " AND status=?"
    sql += " ORDER BY created_at DESC LIMIT 100"
    rows = c.execute(sql, ((status,) if status else ())).fetchall()
    c.close()
    return {"items": [dict(r) for r in rows], "count": len(rows), "steps": GUIDE_ADMIN_REVIEW}


def review_registration(user_id: str, action: str, actor: str, role: str = "student", note: str = "") -> dict:
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="action 只能是 approve／reject")
    if action == "reject" and not (note or "").strip():
        raise HTTPException(status_code=422, detail="驳回须填写理由")
    if action == "approve" and role not in ROLES:
        raise HTTPException(status_code=422, detail="角色只能是 student／counselor／admin／auditor")
    c = db.conn()
    row = c.execute("SELECT * FROM app_user WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(status_code=404, detail="账号不存在")
    if row["status"] != "pending":
        c.close()
        raise HTTPException(status_code=400, detail=f"该申请当前状态为 {row['status']}，不能重复审核")
    new_status = "active" if action == "approve" else "rejected"
    c.execute("UPDATE app_user SET status=?,enabled=?,role=?,reviewed_by=?,reviewed_at=?,review_note=?,"
              "updated_at=? WHERE user_id=?",
              (new_status, 1 if action == "approve" else 0, role if action == "approve" else row["role"],
               actor, db.now(), note, db.now(), user_id))
    c.commit()
    c.close()
    db.audit(actor, "admin", f"register_{action}", user_id, {"role": role, "note": note})
    return {"user_id": user_id, "status": new_status, "role": role if action == "approve" else row["role"],
            "steps": GUIDE_ADMIN_REVIEW}


def reset_password(user_id: str, actor: str) -> dict:
    c = db.conn()
    row = c.execute("SELECT nickname,status FROM app_user WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(status_code=404, detail="账号不存在")
    if row["status"] != "active":
        c.close()
        raise HTTPException(status_code=400, detail="仅已启用的账号可重置密码")
    temp = "Fund@" + "".join(secrets.choice("0123456789") for _ in range(6))
    c.execute("UPDATE app_user SET password_hash=?,must_change_password=1,updated_at=? WHERE user_id=?",
              (_hash(temp), db.now(), user_id))
    c.commit()
    c.close()
    db.audit(actor, "admin", "password_reset", user_id, {"temp": "one-time-shown"})
    return {"user_id": user_id, "nickname": row["nickname"], "temp_password": temp,
            "guidance": GUIDE_RESET, "must_change": True}


def login(nickname: str, password: str) -> dict:
    c = db.conn()
    row = c.execute("SELECT * FROM app_user WHERE nickname=?", ((nickname or "").strip(),)).fetchone()
    if not row or not _verify(password, row["password_hash"]):
        c.close()
        db.audit(nickname or "unknown", "guest", "denied", "login", {"reason": "bad credentials"})
        raise HTTPException(status_code=401, detail="昵称或密码不正确")
    if row["status"] == "pending":
        c.close()
        raise HTTPException(status_code=403, detail="注册申请待审核，请等管理员通过后再登录")
    if row["status"] == "rejected":
        c.close()
        raise HTTPException(status_code=403, detail="注册申请未通过：" + (row["review_note"] or "请联系管理员"))
    if not row["enabled"]:
        c.close()
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    c.execute("INSERT INTO user_session(token,user_id,role,created_at,expires_at) VALUES(?,?,?,?,?)",
              (token, row["user_id"], row["role"], db.now(), expires))
    c.execute("UPDATE app_user SET last_login_at=? WHERE user_id=?", (db.now(), row["user_id"]))
    c.commit()
    c.close()
    db.audit(row["user_id"], row["role"], "login", row["user_id"], {})
    return {"token": token, "role": row["role"], "user": row["user_id"], "nickname": row["nickname"],
            "must_change": bool(row["must_change_password"]), "expires_at": expires}


def logout(token: str | None, actor: str = "") -> None:
    if not token:
        return
    c = db.conn()
    c.execute("DELETE FROM user_session WHERE token=?", (token,))
    c.commit()
    c.close()
    if actor:
        db.audit(actor, "user", "logout", actor, {})


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    c = db.conn()
    row = c.execute("SELECT s.token,s.user_id,s.role,s.expires_at,u.nickname,u.status,u.enabled,"
                    "u.must_change_password FROM user_session s JOIN app_user u ON u.user_id=s.user_id"
                    " WHERE s.token=?", (token,)).fetchone()
    if not row:
        c.close()
        return None
    if row["expires_at"] < db.now():
        c.execute("DELETE FROM user_session WHERE token=?", (token,))
        c.commit()
        c.close()
        return None
    c.close()
    if row["status"] != "active" or not row["enabled"]:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    return {"role": row["role"], "user": row["user_id"], "nickname": row["nickname"],
            "must_change": bool(row["must_change_password"]), "source": "session"}


def change_password(user: dict, old: str, new: str) -> dict:
    if len(new or "") < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
        raise HTTPException(status_code=422, detail="新密码至少 8 位，且同时包含字母和数字")
    c = db.conn()
    row = c.execute("SELECT password_hash FROM app_user WHERE user_id=?", (user["user"],)).fetchone()
    if not row or not _verify(old, row["password_hash"]):
        c.close()
        raise HTTPException(status_code=401, detail="原密码不正确")
    c.execute("UPDATE app_user SET password_hash=?,must_change_password=0,updated_at=? WHERE user_id=?",
              (_hash(new), db.now(), user["user"]))
    c.execute("DELETE FROM user_session WHERE user_id=?", (user["user"],))
    c.commit()
    c.close()
    db.audit(user["user"], user["role"], "password_change", user["user"], {})
    return {"ok": True, "guidance": "密码已修改，其他设备上的登录已失效，请用新密码重新登录。"}
