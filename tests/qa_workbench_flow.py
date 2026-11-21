"""Consultation workbench integration check on copied databases only.

It uses two newly-created student accounts and therefore verifies the new private
consultation routes do not fall back to demo request headers.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8045
BASE = f"http://127.0.0.1:{PORT}"


def call(opener, method, path, body=None, headers=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(), dict(error.headers)


def wait_health(proc):
    for _ in range(40):
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=1):
                return True
        except Exception:
            time.sleep(.25)
    return False


def check(name, condition, detail=""):
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")
    return bool(condition)


def hget(headers, name, default=""):
    """HTTP 头名大小写不敏感（uvicorn 返回小写键）。"""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return default


def create_student(nickname):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    code, raw, _ = call(urllib.request.build_opener(), "POST", "/api/auth/register",
                        {"nickname": nickname, "password": "Abcd1234"})
    if code != 200:
        raise RuntimeError(raw)
    user_id = json.loads(raw)["user_id"]
    code, raw, _ = call(urllib.request.build_opener(), "POST", f"/api/admin/registrations/{user_id}/review",
                        {"action": "approve", "role": "student"}, {"X-Role": "admin", "X-User": "admin_demo"})
    if code != 200:
        raise RuntimeError(raw)
    code, raw, _ = call(opener, "POST", "/api/auth/login", {"nickname": nickname, "password": "Abcd1234"})
    if code != 200:
        raise RuntimeError(raw)
    return opener


def main():
    tmp = Path(tempfile.mkdtemp(prefix="fund_consult_"))
    shutil.copy(ROOT / "data" / "app.db", tmp / "app.db")
    shutil.copy(ROOT / "data" / "staging" / "policies.db", tmp / "policies.db")
    env = dict(os.environ, APP_DB=str(tmp / "app.db"), POLICY_DB=str(tmp / "policies.db"), LOG_LEVEL="WARNING", AUTH_MODE="dev", FUND_LLM_MODE="offline")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(PORT)], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    outcomes = []
    try:
        if not wait_health(proc):
            print(proc.stderr.read().decode("utf-8", "ignore")[-2000:])
            return 1
        guest = urllib.request.build_opener()
        code, _, _ = call(guest, "POST", "/api/qa/sessions")
        outcomes.append(check("无 Cookie 不能创建私人会话", code == 401, str(code)))
        first = create_student("咨询测试甲")
        second = create_student("咨询测试乙")
        code, raw, _ = call(first, "POST", "/api/qa/sessions")
        session = json.loads(raw) if code == 200 else {}
        outcomes.append(check("Cookie 学生创建会话", code == 200 and session.get("id"), str(code)))
        request_id = "consult-flow-001"
        payload = {"question": "国家助学金申请条件", "raw_question": "我想问国家助学金的申请条件", "client_request_id": request_id}
        code, raw, _ = call(first, "POST", f"/api/qa/sessions/{session.get('id')}/turns", payload)
        created = json.loads(raw) if code == 200 else {}
        outcomes.append(check("创建咨询轮次", code == 200 and created.get("created") is True, str(code)))
        code, raw, _ = call(first, "POST", f"/api/qa/sessions/{session.get('id')}/turns", payload)
        repeated = json.loads(raw) if code == 200 else {}
        outcomes.append(check("相同请求只返回原轮次", code == 200 and repeated.get("turn_id") == created.get("turn_id") and not repeated.get("created"), str(code)))
        turn = {}
        for _ in range(60):
            code, raw, _ = call(first, "GET", f"/api/qa/turns/{created.get('turn_id')}")
            turn = json.loads(raw) if code == 200 else {}
            if turn.get("state") in {"completed", "clarified", "refused", "failed"}:
                break
            time.sleep(.15)
        outcomes.append(check("轮次有明确终态和面板快照", turn.get("state") in {"completed", "clarified", "refused"} and bool(turn.get("panel")), turn.get("state", "")))
        raw_text = "我想问国家助学金的申请条件"
        outcomes.append(check("原始提问随创建落库", turn.get("raw_question") == raw_text, str(turn.get("raw_question"))))
        code, raw, _ = call(first, "GET", f"/api/qa/sessions/{session['id']}")
        detail = json.loads(raw) if code == 200 else {}
        outcomes.append(check("刷新读取会话可恢复原始提问", code == 200 and (detail.get("turns") or [{}])[0].get("raw_question") == raw_text, str(code)))
        code, raw, _ = call(first, "PUT", f"/api/qa/turns/{turn['id']}/raw-question", {"raw_question": "修改后的原始问题"})
        updated = json.loads(raw) if code == 200 else {}
        outcomes.append(check("修改原始提问写入数据库", code == 200 and updated.get("raw_question") == "修改后的原始问题", str(code)))
        code, raw, _ = call(first, "GET", f"/api/qa/sessions/{session['id']}")
        detail2 = json.loads(raw) if code == 200 else {}
        outcomes.append(check("修改后重新读取拿到最新值", code == 200 and (detail2.get("turns") or [{}])[0].get("raw_question") == "修改后的原始问题", str(code)))
        code, _, _ = call(first, "PUT", f"/api/qa/turns/{turn['id']}/raw-question", {"raw_question": "x"})
        outcomes.append(check("原始提问长度校验", code == 422, str(code)))
        code, _, _ = call(second, "PUT", f"/api/qa/turns/{turn['id']}/raw-question", {"raw_question": "越权修改"})
        outcomes.append(check("他人不能修改原始提问", code == 404, str(code)))
        code, raw, headers = call(first, "GET", f"/api/qa/turns/{created.get('turn_id')}/events")
        outcomes.append(check("事件流先给依据并以 done 结束", code == 200 and "event: meta" in raw and "event: done" in raw and "event: chunk" in raw and "text/event-stream" in hget(headers, "Content-Type"), str(code)))
        code, _, _ = call(second, "GET", f"/api/qa/sessions/{session.get('id')}")
        outcomes.append(check("另一学生不能读取会话", code == 404, str(code)))
        evidence = (turn.get("panel") or {}).get("evidence") or []
        if evidence:
            code, raw, _ = call(first, "GET", f"/api/qa/turns/{turn['id']}/evidence/{evidence[0]['evidence_id']}")
            item = json.loads(raw) if code == 200 else {}
            outcomes.append(check("原文读取绑定本轮版本", code == 200 and item.get("version_id") == turn["context"].get("policy_version"), str(code)))
        else:
            outcomes.append(check("原文读取绑定本轮版本", False, "没有证据"))
        code, raw, headers = call(first, "GET", f"/api/qa/sessions/{session.get('id')}/export?scope=all")
        outcomes.append(check("全会话导出只含本人记录", code == 200 and "本人政策咨询记录" in raw and "no-store" in hget(headers, "Cache-Control"), str(code)))
        code, raw, _ = call(first, "POST", f"/api/qa/turns/{turn.get('id')}/handoff", {})
        handoff = json.loads(raw) if code == 200 else {}
        outcomes.append(check("咨询到初判只创建最小引导", code == 200 and handoff.get("id")
                              and handoff.get("kind") == "triage" and not handoff.get("task_id"), str(code)))
        code, _, _ = call(second, "GET", f"/api/qa/handoffs/{handoff.get('id')}")
        outcomes.append(check("另一学生不能读取初判引导", code == 404, str(code)))
        sample = json.loads((ROOT / "data" / "samples" / "students.json").read_text(encoding="utf-8"))["complete"]
        body = dict(sample, consent=True, handoff=handoff.get("id"))
        code, raw, _ = call(first, "POST", "/api/triage", body)
        created_task = json.loads(raw) if code == 200 else {}
        outcomes.append(check("引导可用于创建本人初判", code == 200 and created_task.get("task_id"), str(code)))
        code, raw, _ = call(first, "POST", "/api/triage", body)
        outcomes.append(check("同一引导不能重复消费", code == 409, str(code)))
        code, raw, _ = call(first, "POST", f"/api/qa/turns/{turn.get('id')}/progress-handoff",
                            {"task_id": created_task.get("task_id")})
        progress = json.loads(raw) if code == 200 else {}
        outcomes.append(check("进度引导只定位本人任务", code == 200 and progress.get("task_id") == created_task.get("task_id")
                              and "student" not in progress, str(code)))
        return 0 if all(outcomes) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
