"""政策闯关小游戏：接口与页面联检，在临时库副本上跑，不污染真实数据。

用法：python -X utf8 tests\\game_flow.py
退出码 0 表示全部通过。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8047
BASE = f"http://127.0.0.1:{PORT}"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def call(method: str, path: str, body=None):
    path = urllib.parse.quote(path, safe="/?=&%:.-_")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
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
            code, _ = call("GET", "/api/health")
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def answer(level_id: str, choice: str):
    code, raw = call("POST", "/api/game/answer", {"level_id": level_id, "choice": choice})
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, {}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fund-game-"))
    shutil.copy2(ROOT / "data" / "app.db", tmp / "app.db")
    shutil.copy2(ROOT / "data" / "staging" / "policies.db", tmp / "policies.db")
    env = dict(os.environ)
    env["APP_DB"] = str(tmp / "app.db")
    env["POLICY_DB"] = str(tmp / "policies.db")
    env["LOG_LEVEL"] = "WARNING"
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "uvicorn", "backend.app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if not wait_health(proc):
            print("服务未起来：", (proc.stderr.read() or b"").decode("utf-8", "ignore")[:2000])
            return 1

        # ---------- 取关卡 ----------
        code, raw = call("GET", "/api/game/levels")
        data = json.loads(raw) if code == 200 else {}
        levels = data.get("levels", [])
        check("取关卡 200 且 6 关", code == 200 and data.get("total") == 6 and len(levels) == 6,
              f"status={code} total={data.get('total')}")
        check("选项为三档", [o.get("value") for o in data.get("options", [])]
              == ["eligible", "need_check", "not_applicable"], "")
        check("关卡含题面要素",
              all(lv.get("scenario") and lv.get("question") and lv.get("hint")
                  and lv.get("project_name") for lv in levels), "")

        # ---------- 防泄题 ----------
        banned = {"profile", "teach", "correct", "actual", "actual_label", "reasons"}
        leaked = sorted(banned & set().union(*[set(lv) for lv in levels])) if levels else []
        check("关卡不下发画像与答案", not leaked, f"泄露字段={leaked}")
        check("关卡不下发学生画像内容",
              "grade" not in raw and "income_difficulty" not in raw, "")

        # ---------- 三档判定 ----------
        code, d1 = answer("L1", "not_applicable")
        check("L1 一年级国奖判暂不适用",
              code == 200 and d1.get("correct") is True and d1.get("actual") == "not_applicable"
              and d1.get("actual_label") == "暂不适用", f"status={code} actual={d1.get('actual')}")

        code, d2 = answer("L2", "need_check")
        check("L2 认定未出判需核实",
              code == 200 and d2.get("correct") is True and d2.get("actual") == "need_check", "")
        check("L2 解释缺项用中文字段名",
              "家庭经济困难认定结果" in (d2.get("missing_labels") or []),
              str(d2.get("missing_labels")))

        code, d3 = answer("L3", "eligible")
        check("L3 退役士兵判可申请",
              code == 200 and d3.get("correct") is True and d3.get("actual") == "eligible", "")

        code, d4 = answer("L4", "not_applicable")
        check("L4 外省生源判暂不适用",
              code == 200 and d4.get("actual") == "not_applicable", "")

        code, d5 = answer("L5", "eligible")
        check("L5 孤儿特别困难判可申请",
              code == 200 and d5.get("actual") == "eligible", "")

        # ---------- 人工降级与判错 ----------
        code, d6 = answer("L6", "eligible")
        check("L6 已获省专项降为需核实",
              code == 200 and d6.get("actual") == "need_check" and d6.get("correct") is False,
              f"actual={d6.get('actual')}")
        check("判错时仍给出正确结论", d6.get("actual_label") == "需核实", "")

        # ---------- 引用可回点 ----------
        reasons = d3.get("reasons") or []
        ok_span = bool(reasons) and all(
            r.get("clause_id") and r.get("doc_no") and r.get("quote")
            and isinstance(r.get("char_start"), int) and isinstance(r.get("char_end"), int)
            for r in reasons)
        check("依据带条款号与原文偏移", ok_span, f"{len(reasons)} 条")
        if reasons:
            cid = reasons[0]["clause_id"]
            code, raw = call("GET", f"/api/clause/{cid}")
            clause = json.loads(raw) if code == 200 else {}
            check("依据可回点原文", code == 200 and clause.get("text"), f"clause={cid} status={code}")
        else:
            check("依据可回点原文", False, "无依据")

        # ---------- 异常入参 ----------
        code, _ = answer("L99", "eligible")
        check("未知关卡 404", code == 404, f"status={code}")
        code, _ = answer("L1", "maybe")
        check("非法档位 400", code == 400, f"status={code}")
        code, raw = call("POST", "/api/game/answer", {"choice": "eligible"})
        check("缺 level_id 400", code == 400, f"status={code}")

        # ---------- 页面与资源 ----------
        code, raw = call("GET", "/game.html")
        check("闯关页 200 且含免责声明",
              code == 200 and "政策闯关" in raw and "仅供参考" in raw, f"status={code}")
        for asset in ("/static/game.css", "/static/game.js"):
            code, _ = call("GET", asset)
            check(f"资源 {asset} 200", code == 200, f"status={code}")

        # ---------- 导航入口 ----------
        for page in ("/index.html", "/triage.html", "/qa.html", "/progress.html"):
            code, raw = call("GET", page)
            check(f"{page} 有闯关入口", code == 200 and 'href="game.html"' in raw, f"status={code}")
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
