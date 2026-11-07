"""接口冒烟：在临时库副本上跑三端主流程，不污染 data/ 下的真实数据。

用法：python -X utf8 tests\smoke.py
退出码 0 表示全部通过。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8021
BASE = f"http://127.0.0.1:{PORT}"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def call(method: str, path: str, body=None, role="student", user="student_demo", base=BASE):
    path = urllib.parse.quote(path, safe="/?=&%:.-_")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("X-Role", role)
    req.add_header("X-User", user)
    if data:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def wait_health(proc: subprocess.Popen, tries: int = 25) -> bool:
    for _ in range(tries):
        if proc.poll() is not None:
            return False
        try:
            code, _ = call("GET", "/api/health", base=BASE)
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fund-smoke-"))
    shutil.copy2(ROOT / "data" / "app.db", tmp / "app.db")
    shutil.copy2(ROOT / "data" / "staging" / "policies.db", tmp / "policies.db")
    env = dict(os.environ)
    env["APP_DB"] = str(tmp / "app.db")
    env["POLICY_DB"] = str(tmp / "policies.db")
    env["LOG_LEVEL"] = "WARNING"  # 冒烟不采集访问日志，避免管道写满阻塞服务
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "uvicorn", "backend.app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if not wait_health(proc):
            print("服务未起来：", (proc.stderr.read() or b"").decode("utf-8", "ignore")[:2000])
            return 1

        code, _ = call("GET", "/api/health")
        check("健康检查 200", code == 200, f"status={code}")

        code, _ = call("POST", "/api/triage", {"grade": 2})
        check("未勾选授权被拒 400", code == 400, f"status={code}")

        sample = json.loads((ROOT / "data" / "samples" / "students.json").read_text(encoding="utf-8"))["complete"]
        sample["consent"] = True
        code, raw = call("POST", "/api/triage", sample)
        data = json.loads(raw) if code == 200 else {}
        check("初判 200 且命中 4 项", code == 200 and data.get("hit_count") == 4,
              f"status={code} hits={data.get('hit_count')}")
        task_id = data.get("task_id")
        labels = {i["project_id"]: i["conclusion_label"] for i in data.get("items", []) if i.get("conclusion")}
        check("三档结论齐全", labels.get("GZ") == "可申请" and labels.get("LZ") == "需核实"
              and labels.get("XS") == "暂不适用", str(labels))
        check("响应不含姓名", "name" not in data.get("student", {}), "")

        code, raw = call("POST", "/api/triage", {"grade": 99, "consent": True})
        check("越界参数 422", code == 422, f"status={code}")
        check("越界提示含范围", "1–8" in raw, raw[:80])

        code, raw = call("GET", f"/api/triage/{task_id}")
        check("查任务 200 且状态 triaged", code == 200 and json.loads(raw).get("status") == "triaged", f"status={code}")

        code, _ = call("POST", f"/api/triage/{task_id}/submit")
        check("提交复核 200", code == 200, f"status={code}")

        code, _ = call("POST", f"/api/triage/{task_id}/review",
                       {"action": "return", "reason": ""}, role="counselor", user="counselor_demo")
        check("退回缺理由 422", code == 422, f"status={code}")

        code, _ = call("POST", f"/api/triage/{task_id}/review",
                       {"action": "approve", "reason": ""}, role="counselor", user="counselor_demo")
        check("复核通过 200", code == 200, f"status={code}")

        code, _ = call("POST", f"/api/triage/{task_id}/appeal", {"reason": "本人已提交低保证明，请复核"})
        check("申诉 200", code == 200, f"status={code}")

        code, raw = call("GET", "/api/appeals", role="admin", user="admin_demo")
        check("管理员见申诉", code == 200 and json.loads(raw)["count"] >= 1, f"status={code}")
        appeal_id = json.loads(raw)["items"][0]["id"]

        code, _ = call("POST", f"/api/appeal/{appeal_id}/handle",
                       {"decision": "amend", "reason": "补充低保证明后改判", "new_conclusion": "可申请"},
                       role="admin", user="admin_demo")
        check("申诉改判 200", code == 200, f"status={code}")

        code, raw = call("GET", f"/api/triage/{task_id}")
        check("改判后状态 amended", code == 200 and json.loads(raw)["status"] == "amended", f"status={code}")

        code, raw = call("GET", "/api/tasks?status=reviewing", role="counselor", user="counselor_demo")
        check("审核待办可查", code == 200, f"status={code} count={json.loads(raw)['count'] if code == 200 else '-'}")

        code, raw = call("GET", "/api/board", role="counselor", user="counselor_demo")
        check("看板 200", code == 200 and json.loads(raw)["tasks"] >= 1, f"status={code}")

        # --- C3 批量初筛 ---
        batch_ids = []
        for _ in range(2):
            code, raw = call("POST", "/api/triage", dict(sample))
            tid = json.loads(raw)["task_id"]
            call("POST", f"/api/triage/{tid}/submit")
            batch_ids.append(tid)

        code, raw = call("GET", "/api/review/queue?status=reviewing&limit=50",
                         role="counselor", user="counselor_demo")
        queue = json.loads(raw) if code == 200 else {}
        check("批量队列带等待时长", code == 200 and queue.get("count", 0) >= 2
              and all("wait_hours" in i for i in queue.get("items", [])), f"count={queue.get('count')}")

        code, _ = call("POST", "/api/review/batch", {"task_ids": batch_ids, "action": "return", "reason": ""},
                       role="counselor", user="counselor_demo")
        check("批量退回缺理由 422", code == 422, f"status={code}")

        code, raw = call("POST", "/api/review/batch", {"task_ids": batch_ids, "action": "approve", "reason": ""},
                         role="counselor", user="counselor_demo")
        res = json.loads(raw) if code == 200 else {}
        check("批量通过 2 条", code == 200 and res.get("count_ok") == 2, f"ok={res.get('count_ok')}")

        code, raw = call("POST", "/api/review/batch", {"task_ids": batch_ids, "action": "approve", "reason": ""},
                         role="counselor", user="counselor_demo")
        res = json.loads(raw) if code == 200 else {}
        check("重复批量被跳过", code == 200 and res.get("count_skipped") == 2, f"skipped={res.get('count_skipped')}")

        code, raw = call("GET", "/api/board/levels", role="counselor", user="counselor_demo")
        levels = json.loads(raw) if code == 200 else {}
        check("两级看板含项目分布", code == 200 and levels.get("school", {}).get("tasks", 0) >= 1
              and len(levels.get("projects", [])) >= 5, f"projects={len(levels.get('projects', []))}")

        code, raw = call("GET", "/api/warnings", role="counselor", user="counselor_demo")
        warn = json.loads(raw) if code == 200 else {}
        check("预警含未核文提示", code == 200 and any(w["code"] == "policy_unverified" for w in warn.get("items", [])),
              f"codes={[w['code'] for w in warn.get('items', [])]}")

        # --- D6 报送报表 ---
        code, raw = call("GET", "/api/report/summary?days=30", role="admin", user="admin_demo")
        rep = json.loads(raw) if code == 200 else {}
        check("报送报表 200", code == 200 and rep.get("tasks", 0) >= 1 and rep.get("policy_version"),
              f"tasks={rep.get('tasks')} version={rep.get('policy_version')}")

        code, raw = call("GET", "/api/report/export.csv?days=30", role="admin", user="admin_demo")
        check("报表 CSV 含表头", code == 200 and "任务号,创建时间" in raw, f"status={code}")

        code, _ = call("GET", "/api/report/summary", role="counselor", user="counselor_demo")
        check("学生/辅导员不得取报表", code == 403, f"status={code}")

        # --- D1 账号权限 ---
        code, raw = call("GET", "/api/users", role="admin", user="admin_demo")
        users = json.loads(raw) if code == 200 else {}
        items = users.get("items", [])
        has_admin = any(x.get("nickname") == "admin" and x.get("role") == "admin" for x in items)
        check("账号列表含默认管理员", code == 200 and users.get("count", 0) >= 5 and has_admin,
              f"count={users.get('count')} admin={has_admin}")

        code, _ = call("POST", "/api/users/counselor_demo/enable", {"enabled": False},
                       role="admin", user="admin_demo")
        check("停用账号 200", code == 200, f"status={code}")
        code, _ = call("GET", "/api/tasks?status=reviewing", role="counselor", user="counselor_demo")
        check("停用后一律 403", code == 403, f"status={code}")
        code, _ = call("POST", "/api/users/counselor_demo/enable", {"enabled": True},
                       role="admin", user="admin_demo")
        check("启用账号 200", code == 200, f"status={code}")

        code, _ = call("POST", "/api/users/admin_demo/enable", {"enabled": False},
                       role="admin", user="admin")
        check("停用普通管理员 200", code == 200, f"status={code}")

        code, _ = call("POST", "/api/users/admin/enable", {"enabled": False},
                       role="admin", user="admin")
        check("最后一名管理员不可停用", code == 400, f"status={code}")

        code, _ = call("POST", "/api/users/admin_demo/enable", {"enabled": True},
                       role="admin", user="admin")
        check("恢复管理员 200", code == 200, f"status={code}")

        # --- D6 导出审批 ---
        code, raw = call("POST", "/api/export-requests", {"task_id": task_id, "note": "报送学院材料"},
                         role="student", user="student_demo")
        er = json.loads(raw) if code == 200 else {}
        check("导出申请 200", code == 200 and er.get("status") == "pending", f"status={code}")

        code, _ = call("GET", "/api/export-requests", role="student", user="student_demo")
        check("学生不得查导出申请", code == 403, f"status={code}")

        code, raw = call("GET", "/api/export-requests?status=pending", role="admin", user="admin_demo")
        pend = json.loads(raw) if code == 200 else {}
        req_id = pend["items"][0]["id"] if pend.get("count") else None
        code, _ = call("POST", f"/api/export-requests/{req_id}/review", {"action": "approve", "note": "同意"},
                       role="admin", user="admin_demo")
        check("导出审批 200", code == 200, f"status={code}")

        code, raw = call("GET", f"/api/export/{task_id}")
        check("导出件带审批人", code == 200 and "导出审批：已批准" in raw, f"status={code}")

        code, raw = call("GET", "/api/policy/docs", role="admin", user="admin_demo")
        docs = json.loads(raw)["items"] if code == 200 else []
        check("政策清单 19 份", code == 200 and len(docs) == 19, f"count={len(docs)}")
        target = next(d for d in docs if d["status"] == "pending_verify")

        code, raw = call("POST", f"/api/policy/{target['id']}/publish", role="admin", user="admin_demo")
        check("未核文不得发布 400", code == 400, f"status={code} {raw[:60]}")

        code, _ = call("POST", f"/api/policy/{target['id']}/verify",
                       {"doc_no": target["doc_no"] or "粤财规〔2026〕1号", "issuer": "广东省财政厅等五部门",
                        "effective_from": "2026-03-01", "verifier": "admin_demo"},
                       role="admin", user="admin_demo")
        check("核文 200", code == 200, f"status={code}")

        code, raw = call("POST", f"/api/policy/{target['id']}/publish", role="admin", user="admin_demo")
        published = json.loads(raw) if code == 200 else {}
        version = published.get("version_id")
        check("发布生成版本", code == 200 and version, f"status={code} version={version}")
        check("发布条款只算已核文件", 0 < published.get("clauses", 0) < 342,
              f"docs={published.get('docs')} clauses={published.get('clauses')}")

        code, raw = call("GET", "/api/policy/versions", role="admin", user="admin_demo")
        check("版本列表含 current", code == 200 and any(v["status"] == "current" for v in json.loads(raw)["items"]),
              f"status={code}")

        code, raw = call("POST", "/api/policy/rollback", {"version_id": version}, role="admin", user="admin_demo")
        check("回滚 200", code == 200, f"status={code}")

        code, raw = call("POST", "/api/triage", dict(sample))
        anchored = json.loads(raw).get("policy_version") if code == 200 else None
        check("新任务锚定发布版本", code == 200 and anchored == version, f"version={anchored}")

        code, raw = call("POST", "/api/qa", {"question": "国家助学金"})
        qa = json.loads(raw) if code == 200 else {}
        check("问答命中条款", code == 200 and not qa.get("refused") and qa.get("hits"), f"status={code} hits={len(qa.get('hits', []))}")
        check("问答标注离线模式", qa.get("mode") == "offline" and "离线规则模式" in (qa.get("answer") or ""),
              f"mode={qa.get('mode')}")
        check("问答引用可回点", bool(qa.get("citations")) and all(
            c.get("clause_id") and c.get("span") for c in qa.get("citations", [])), f"citations={len(qa.get('citations', []))}")

        code, raw = call("GET", "/api/provider")
        prov = json.loads(raw) if code == 200 else {}
        check("Provider 三态可查", code == 200 and prov.get("mode") == "offline"
              and prov.get("prompt_version") == "qa_v1", f"mode={prov.get('mode')}")

        code, raw = call("POST", "/api/qa", {"question": "火星上怎么申请助学金"})
        off = json.loads(raw) if code == 200 else {}
        check("越界提问按规则拒答", code == 200 and off.get("refused") and off.get("reason") == "off_domain",
              f"reason={off.get('reason')}")

        code, raw = call("POST", "/api/qa", {"question": "火星上怎么申请助学金"})
        qa = json.loads(raw) if code == 200 else {}
        check("无依据拒答", code == 200 and qa.get("refused") is True, f"refused={qa.get('refused')}")

        code, raw = call("GET", "/api/health")
        health = json.loads(raw) if code == 200 else {}
        check("健康检查标注模式", code == 200 and health.get("llm_mode") == "offline"
              and health.get("auth_mode") == "dev", f"llm={health.get('llm_mode')} auth={health.get('auth_mode')}")

        code, raw = call("GET", "/api/auth/whoami")
        me = json.loads(raw) if code == 200 else {}
        check("统一身份占位 whoami", code == 200 and me.get("mode") == "dev" and me.get("role") == "student",
              f"mode={me.get('mode')}")

        code, _ = call("GET", "/api/auth/whoami", role="root", user="nobody")
        check("非法角色 whoami 403", code == 403, f"status={code}")

        code, raw = call("GET", "/api/policy/search?q=助学金")
        check("条款检索 200", code == 200 and json.loads(raw)["count"] >= 1, f"status={code}")

        code, raw = call("GET", "/api/materials?project_id=GZ")
        check("材料带来源类型", code == 200 and all(m["source_type"] for m in json.loads(raw)["items"]), f"status={code}")

        code, _ = call("GET", "/api/school-tiers")
        check("分档接口说明未配置", code == 200, f"status={code}")

        code, _ = call("POST", "/api/school-tiers", {"level": "一等", "income_band": "低保", "amount": 5000},
                       role="admin", user="admin_demo")
        check("新增分档 200", code == 200, f"status={code}")

        code, raw = call("GET", "/api/audit", role="admin", user="admin_demo")
        actions = {a["action"] for a in json.loads(raw)["items"]} if code == 200 else set()
        check("审计记录了初判与越权", {"triage", "denied"} & actions, f"actions={sorted(actions)[:6]}")

        code, _ = call("GET", "/api/audit")
        check("学生查审计 403", code == 403, f"status={code}")

        code, raw = call("GET", f"/api/export/{task_id}")
        check("导出含依据与声明", code == 200 and "依据原文" in raw and "仅供参考" in raw, f"status={code}")

        for path, name in (("/", "学生端"), ("/review.html", "审核工作台"), ("/admin.html", "管理后台")):
            code, raw = call("GET", path)
            check(f"页面 {path} 200", code == 200 and name in raw, f"status={code}")

        # 审计表禁改禁删
        c = sqlite3.connect(str(tmp / "app.db"))
        try:
            c.execute("UPDATE audit_log SET action='tampered' WHERE id=1")
            tamper = True
        except sqlite3.DatabaseError:
            tamper = False
        c.close()
        check("审计表拒绝改写", not tamper, "")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{len(RESULTS)} 项检查，通过 {passed}，失败 {len(RESULTS) - passed}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
