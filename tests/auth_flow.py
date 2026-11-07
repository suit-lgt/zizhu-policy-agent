"""账号闭环自测：注册 → 待审核不能登录 → 管理员通过 → 登录 → 重置密码 → 改密。

独立起服务（tempdir 库副本，端口 8033），不写真实库；退出码 0 表示全过。
用法：python -X utf8 tests/auth_flow.py
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8033
BASE = f"http://127.0.0.1:{PORT}"
PASS, FAIL = 0, 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}  {extra}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {extra}")


def wait_health(proc: subprocess.Popen, tries: int = 30) -> bool:
    for _ in range(tries):
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fund_auth_"))
    shutil.copy(ROOT / "data" / "app.db", tmp / "app.db")
    shutil.copy(ROOT / "data" / "staging" / "policies.db", tmp / "policies.db")
    env = dict(os.environ, APP_DB=str(tmp / "app.db"), POLICY_DB=str(tmp / "policies.db"),
               LOG_LEVEL="WARNING", AUTH_MODE="dev")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "backend.app.main:app",
                             "--host", "127.0.0.1", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    if not wait_health(proc):
        err = proc.stderr.read().decode("utf-8", "ignore")[-800:] if proc.stderr else ""
        print("服务未能启动：", err)
        return 1
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method: str, path: str, body=None, headers=None, use_jar=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            op = opener if use_jar else urllib.request.build_opener()
            with op.open(req, timeout=20) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    try:
        code, raw = call("POST", "/api/auth/register",
                         {"nickname": "测试同学甲", "password": "Abcd1234",
                          "school": "深圳信息职业技术大学", "phone": "13800138000"})
        reg = json.loads(raw) if raw else {}
        check("注册提交成功", code == 200 and reg.get("status") == "pending" and reg.get("user_id"),
              f"status={code} id={reg.get('user_id')}")
        check("注册返回四步指引", len(reg.get("steps") or []) == 4, f"steps={len(reg.get('steps') or [])}")

        code, _ = call("POST", "/api/auth/register",
                       {"nickname": "测试同学甲", "password": "Abcd1234"})
        check("昵称重复被拒", code == 409, f"status={code}")
        code, _ = call("POST", "/api/auth/register", {"nickname": "甲", "password": "abc"})
        check("弱密码被拒", code == 422, f"status={code}")

        code, raw = call("POST", "/api/auth/login", {"nickname": "测试同学甲", "password": "Abcd1234"})
        check("待审核不能登录", code == 403, f"status={code}")

        staff = {"X-Role": "admin", "X-User": "admin_demo"}
        code, raw = call("GET", "/api/admin/registrations?status=pending", headers=staff)
        items = json.loads(raw)["items"] if code == 200 else []
        uid = next((i["user_id"] for i in items if i["nickname"] == "测试同学甲"), None)
        check("管理员看到注册申请", bool(uid), f"count={len(items)}")

        code, raw = call("POST", f"/api/admin/registrations/{uid}/review",
                         {"action": "reject", "note": ""}, headers=staff)
        check("驳回缺理由被拒", code == 422, f"status={code}")

        code, raw = call("POST", f"/api/admin/registrations/{uid}/review",
                         {"action": "approve", "role": "student"}, headers=staff)
        check("管理员通过注册", code == 200 and json.loads(raw).get("status") == "active",
              f"status={code}")

        code, raw = call("POST", "/api/auth/login", {"nickname": "测试同学甲", "password": "Abcd1234"},
                         use_jar=True)
        check("审核后可登录", code == 200 and json.loads(raw).get("role") == "student", f"status={code}")

        code, raw = call("GET", "/api/auth/me", use_jar=True)
        me = json.loads(raw) if code == 200 else {}
        check("会话身份正确", code == 200 and me.get("logged_in") and me.get("nickname") == "测试同学甲",
              f"me={me.get('nickname')}")

        code, raw = call("POST", f"/api/admin/users/{uid}/reset-password", headers=staff)
        temp = json.loads(raw) if code == 200 else {}
        check("管理员重置密码返回临时密码", code == 200 and temp.get("temp_password"),
              f"temp={temp.get('temp_password')}")

        code, _ = call("POST", "/api/auth/login", {"nickname": "测试同学甲", "password": "Abcd1234"})
        check("旧密码登录失败", code == 401, f"status={code}")

        code, raw = call("POST", "/api/auth/login",
                         {"nickname": "测试同学甲", "password": temp.get("temp_password")}, use_jar=True)
        check("临时密码可登录且提示改密", code == 200 and json.loads(raw).get("must_change") is True,
              f"status={code}")

        code, raw = call("POST", "/api/auth/change-password",
                         {"old_password": temp.get("temp_password"), "new_password": "Newpass123"},
                         use_jar=True)
        check("本人可改密", code == 200, f"status={code}")

        code, raw = call("POST", "/api/auth/login",
                         {"nickname": "测试同学甲", "password": "Newpass123"}, use_jar=True)
        check("新密码可登录", code == 200, f"status={code}")

        code, raw = call("GET", "/api/auth/me", use_jar=True, headers={"X-Role": "admin"})
        me = json.loads(raw) if code == 200 else {}
        check("会话优先于请求头", code == 200 and me.get("role") == "student", f"role={me.get('role')}")

        code, _ = call("GET", "/api/users", use_jar=True, headers={"X-Role": "admin"})
        check("学生会话访问管理接口 403", code == 403, f"status={code}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASS + FAIL} 项检查，通过 {PASS}，失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
