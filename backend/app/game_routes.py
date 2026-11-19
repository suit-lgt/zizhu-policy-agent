"""政策闯关小游戏 API：题干与答案分离，答案由 triage 现算。

设计约束：
- 取关卡不下发学生画像、解析与正确答案，避免前端抄答案；
- 提交作答时才调用 triage_one()，三档结论仍由确定性代码给出（I-2）；
- 引用原样透传后端拼装的 reasons（含 clause_id 与原文偏移），前端只渲染（I-3）；
- 不写数据库、不需要登录，作答记录只留在浏览器本地。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "tools"))

import triage as tg  # noqa: E402

LEVELS_PATH = ROOT / "data" / "game" / "levels.json"
FIELDS_PATH = ROOT / "data" / "fields.json"
CHOICES = ("eligible", "need_check", "not_applicable")

router = APIRouter(prefix="/api/game")


def _field_labels() -> dict:
    """字段代号 → 学生看得懂的中文名，用于解释「还缺什么」。"""
    try:
        data = json.loads(FIELDS_PATH.read_text(encoding="utf-8"))
        return {f["code"]: f["name"] for f in data["fields"]}
    except Exception:  # noqa: BLE001
        return {}


def _levels() -> dict:
    """每次读盘：题库只有几 KB，换取改题后无需重启。"""
    try:
        return json.loads(LEVELS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(503, "题库文件缺失") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(503, "题库格式有误") from exc


def _projects() -> dict:
    try:
        return {p["code"]: p for p in tg.load_projects()["projects"]}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "资助项目配置不可用") from exc


@router.get("/levels")
def list_levels():
    data = _levels()
    projects = _projects()
    items = []
    for i, lv in enumerate(data["levels"]):
        items.append({
            "level_id": lv["level_id"],
            "index": i + 1,
            "title": lv["title"],
            "project_name": projects.get(lv["project_code"], {}).get("name", ""),
            "scenario": lv["scenario"],
            "question": lv["question"],
            "hint": lv["hint"],
        })
    return {"total": len(items), "options": data["options"], "levels": items}


@router.post("/answer")
def submit_answer(payload: dict = Body(default=None)):
    payload = payload or {}
    level_id = str(payload.get("level_id") or "").strip()
    choice = str(payload.get("choice") or "").strip()
    if choice not in CHOICES:
        raise HTTPException(400, "choice 必须是 eligible／need_check／not_applicable")
    if not level_id:
        raise HTTPException(400, "缺少 level_id")

    data = _levels()
    level = next((lv for lv in data["levels"] if lv["level_id"] == level_id), None)
    if level is None:
        raise HTTPException(404, "关卡不存在")

    project = _projects().get(level["project_code"])
    if project is None:
        raise HTTPException(503, "关卡对应的资助项目未配置")
    try:
        atoms, _stats = tg.load_atoms()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "政策库暂不可用") from exc

    item = tg.triage_one(project, atoms, level["profile"])
    actual = item.get("conclusion")
    if not actual:
        # I-1：没有条款依据就不出结论，游戏也不给答案
        raise HTTPException(503, "该关卡暂无可用条款，不作判定")

    labels = tg.CONCLUSION_LABEL
    field_names = _field_labels()
    return {
        "level_id": level_id,
        "correct": choice == actual,
        "chosen": choice,
        "chosen_label": labels[choice],
        "actual": actual,
        "actual_label": labels[actual],
        "project_name": item["project_name"],
        "teach": level["teach"],
        "reasons": item["reasons"],
        "missing_fields": item["missing_fields"],
        "missing_labels": [field_names.get(f, f) for f in item["missing_fields"]],
        "materials": item["materials"],
        "standard_note": item["standard_note"],
        "notes": item["notes"],
    }
