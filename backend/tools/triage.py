"""R-003 确定性判等：把学生勾选字段映射成三档结论（可申请／需核实／暂不适用）。

规则来源与边界：
- 只用 `policy_atom` 中 superseded=0 的原子；被替代的旧标准不参与判等（D-010）。
- 金额类（role=标准）只展示，不判等；认定类（determinable=需认定）缺结论即需核实；
  评审类（determinable=需评审）不由学生自填，恒为需核实（I-2）。
- 每个结论必须挂 ≥1 条可回点原文的条款，否则不出结论（I-1）。
- 输出永不出现"符合／不符合"，只出 可申请／需核实／暂不适用。

用法：
  python -X utf8 tools/triage.py --input data/samples/students.json
  python -X utf8 tools/triage.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomize as az  # noqa: E402
import policy_collector as pc  # noqa: E402

ROOT = pc.ROOT
PROJECTS_PATH = ROOT / "data" / "projects.json"
SAMPLES_PATH = ROOT / "data" / "samples" / "students.json"

CONCLUSION_LABEL = {"eligible": "可申请", "need_check": "需核实", "not_applicable": "暂不适用"}
HIT = ("eligible", "need_check")


def load_projects() -> dict:
    return json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))


def load_atoms(version_id: str | None = None, db_path=None) -> tuple[dict, dict]:
    """返回 (现行原子字典, 统计)。只取 superseded=0。

    传入 version_id 时只读该发布版本的原子快照；否则读现行 policy_atom。
    """
    conn = az.connect(db_path)
    if version_id:
        rows = conn.execute(
            "SELECT a.atom_id,a.project_code,a.field,a.op,a.value,a.role,a.determinable,"
            "a.quote,a.quote_start,a.quote_end,a.clause_id,a.doc_no,a.doc_id,c.clause_no,p.title "
            "FROM policy_snapshot_atom a LEFT JOIN policy_clause c ON c.id=a.clause_id "
            "LEFT JOIN policy_staging p ON p.id=a.doc_id "
            "WHERE a.version_id=? AND a.superseded=0",
            (version_id,)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM policy_snapshot_atom WHERE version_id=?",
                             (version_id,)).fetchone()[0]
    else:
        rows = conn.execute(
            "SELECT a.atom_id,a.project_code,a.field,a.op,a.value,a.role,a.determinable,"
            "a.quote,a.quote_start,a.quote_end,a.clause_id,a.doc_no,a.doc_id,c.clause_no,p.title "
            "FROM policy_atom a LEFT JOIN policy_clause c ON c.id=a.clause_id "
            "LEFT JOIN policy_staging p ON p.id=a.doc_id WHERE a.superseded=0"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM policy_atom").fetchone()[0]
    atoms = {
        r[0]: {
            "atom_id": r[0], "project_code": r[1], "field": r[2], "op": r[3], "value": r[4],
            "role": r[5], "determinable": r[6], "quote": r[7], "quote_start": r[8],
            "quote_end": r[9], "clause_id": r[10], "doc_no": r[11], "doc_id": r[12],
            "clause_no": r[13] or "", "doc_title": r[14] or "",
        }
        for r in rows
    }
    conn.close()
    return atoms, {"total": total, "current": len(atoms), "excluded_superseded": total - len(atoms)}


def validate_student(student: dict) -> list[str]:
    fields = {f["code"]: f for f in json.loads(
        (ROOT / "data" / "fields.json").read_text(encoding="utf-8"))["fields"]}
    errors = []
    for k, v in student.items():
        if k in ("name", "note"):
            continue
        f = fields.get(k)
        if not f:
            errors.append(f"字段不在字典：{k}")
            continue
        if f["type"] == "enum" and "values" in f and v not in f["values"]:
            errors.append(f"取值不在枚举：{k}={v}")
        if f["type"] == "bool" and str(v).lower() not in ("true", "false"):
            errors.append(f"布尔取值非法：{k}={v}")
        if f["type"] == "int":
            if not str(v).isdigit():
                errors.append(f"整数取值非法：{k}={v}")
            elif not 1 <= int(v) <= 8:
                errors.append(f"整数取值超出范围：{k}={v}（允许 1–8）")
    return errors


def eval_atom(atom: dict, student: dict) -> tuple[str, str]:
    """返回 (true/false/unknown/display, 说明)。"""
    if atom["role"] in ("标准", "定义") or atom["op"] in ("依据", "区间"):
        return "display", ""
    if atom["determinable"] == "需评审":
        return "unknown", "须经学校评审确定，不能由本人自评"
    f = atom["field"]
    v = student.get(f)
    if v is None or str(v) == "" or str(v) in ("不清楚", "未定"):
        return "unknown", f"未填「{f}」"
    if f == "income_difficulty":
        if v == "已认定困难":
            return "true", ""
        if v == "待认定":
            return "unknown", "家庭经济困难认定尚在办理中"
        return "false", "尚未取得家庭经济困难认定结论"
    if f == "special_hardship" and atom["value"] == "特别困难":
        return ("true", "") if v == "特别困难" else ("false", f"当前困难等级为「{v}」，非特别困难")
    if atom["op"] == "=":
        ok = str(v).lower() == str(atom["value"]).lower()
        return ("true", "") if ok else ("false", f"「{f}」为 {v}，要求 {atom['value']}")
    try:
        a, b = int(v), int(atom["value"])
    except (TypeError, ValueError):
        return "unknown", f"「{f}」不是可比较的整数"
    ops = {">=": a >= b, ">": a > b, "<=": a <= b, "<": a < b}
    ok = ops.get(atom["op"], False)
    return ("true", "") if ok else ("false", f"「{f}」为 {v}，要求 {atom['op']}{atom['value']}")


def make_reason(atom: dict) -> dict:
    return {
        "clause_id": atom["clause_id"],
        "policy_title": atom["doc_title"],
        "doc_no": atom["doc_no"],
        "article_no": atom["clause_no"],
        "quote": atom["quote"],
        "char_start": atom["quote_start"],
        "char_end": atom["quote_end"],
    }


def dedupe_reasons(reasons: list[dict]) -> list[dict]:
    """同一 clause 内，较短片段若被较长片段完整包含，则只保留较长者（引用仍可回点原文）。"""
    kept: list[dict] = []
    for r in sorted(reasons, key=lambda x: -len(x["quote"])):
        if any(r["clause_id"] == k["clause_id"] and r["quote"] in k["quote"] for k in kept):
            continue
        kept.append(r)
    return kept


def dedupe_quotes(quotes: list[str]) -> list[str]:
    return [q for q in quotes if not any(q != other and q in other for other in quotes)]


def triage_one(project: dict, atoms: dict, student: dict) -> dict:
    referenced = {aid for path in project["paths"] for aid in path} | set(project.get("manual", []))
    missing_atoms = [aid for aid in referenced if aid not in atoms]
    results = {aid: eval_atom(atoms[aid], student) for aid in referenced if aid in atoms}

    path_states = []
    for path in project["paths"]:
        states = [results.get(aid, ("unknown", ""))[0] for aid in path]
        if "false" in states:
            path_states.append("false")
        elif "unknown" in states:
            path_states.append("unknown")
        else:
            path_states.append("true")

    if any(s == "true" for s in path_states):
        conclusion, basis = "eligible", "true"
    elif all(s == "false" for s in path_states):
        conclusion, basis = "not_applicable", "false"
    else:
        conclusion, basis = "need_check", "unknown"

    notes, reasons = [], []
    # 需人工确认的原子（如"原则上不再享受"）：命中即降级
    for aid in project.get("manual", []):
        if aid not in atoms:
            continue
        state, why = results[aid]
        if state == "true":
            conclusion = "need_check"
            notes.append(f"{aid}：{atoms[aid]['quote']}——原则上排除，需人工确认")
            reasons.append(make_reason(atoms[aid]))
        elif state == "unknown":
            conclusion = "need_check" if conclusion != "not_applicable" else conclusion
            notes.append(f"{aid}：{why}，无法确认是否重复享受")

    # 依据：可申请=满足的原子；需核实=未确定的原子（附带已满足的作为背景）；暂不适用=不满足的原子
    want = {
        "eligible": ("true",),
        "need_check": ("unknown", "true"),
        "not_applicable": ("false",),
    }[conclusion]
    for aid in sorted(referenced):
        if aid not in atoms or aid in project.get("manual", []):
            continue
        state, why = results[aid]
        if state in want:
            reasons.append(make_reason(atoms[aid]))
            if state == "unknown" and why:
                notes.append(f"{aid}：{why}")

    reasons = dedupe_reasons(reasons)
    if not reasons and missing_atoms:
        # 原子缺失时不得出结论（I-1）
        return {
            "project_id": project["code"], "project_name": project["name"],
            "conclusion": None, "conclusion_label": "未匹配",
            "reasons": [], "note": f"缺少原子定义：{','.join(missing_atoms)}，不出结论",
        }

    missing_fields = sorted({
        atoms[aid]["field"] for aid, (state, _) in results.items()
        if state == "unknown" and atoms[aid]["determinable"] != "需评审"
    })
    review_required = any(atoms[aid]["determinable"] == "需评审" for aid in results)
    standards = dedupe_quotes([atoms[aid]["quote"] for aid in project.get("standards", []) if aid in atoms])

    item = {
        "project_id": project["code"],
        "project_name": project["name"],
        "conclusion": conclusion,
        "conclusion_label": CONCLUSION_LABEL[conclusion],
        "reasons": reasons,
        "missing_fields": missing_fields,
        "review_required": review_required,
        "standards": standards,
        "standard_note": project.get("standard_note", ""),
        "materials": project.get("materials", []),
        "channel": project.get("channel", ""),
        "notes": notes,
    }
    return item


def triage(student: dict, projects: dict | None = None, atoms: dict | None = None,
           version_id: str | None = None, db_path=None) -> dict:
    projects = projects or load_projects()
    if atoms is None:
        atoms, atom_stats = load_atoms(version_id, db_path)
    else:
        atom_stats = None
    errors = validate_student(student)
    if errors:
        return {"error": errors}
    stage = student.get("stage")
    items = []
    for project in projects["projects"]:
        if stage and stage not in projects["stages"]:
            items.append({
                "project_id": project["code"], "project_name": project["name"],
                "conclusion": "not_applicable", "conclusion_label": "暂不适用",
                "reasons": [], "missing_fields": [], "notes": [f"学段「{stage}」不在本知识库覆盖范围"],
                "materials": [], "channel": "",
            })
            continue
        items.append(triage_one(project, atoms, student))

    hits = [i for i in items if i.get("conclusion") in HIT]
    fallback = ""
    if not hits:
        fallback = "当前版本未匹配到可申请或需核实的项目；建议联系学校资助中心（线下）或在学校学生资助管理系统提交咨询。"

    return {
        "task_id": f"task-{uuid.uuid4().hex[:12]}",
        "policy_version": projects["policy_version"],
        "policy_verified": False,
        "disclaimer": projects["disclaimer"],
        "student": student,
        "items": items,
        "hit_count": len(hits),
        "fallback": fallback,
        "meta": {
            "projects": len(projects["projects"]),
            "atoms_current": atom_stats["current"] if atom_stats else None,
            "atoms_superseded_excluded": atom_stats["excluded_superseded"] if atom_stats else None,
            "materials_basis": projects["materials_basis"],
        },
    }


def run_selftest() -> int:
    projects = load_projects()
    atoms, stats = load_atoms()
    cases = json.loads(SAMPLES_PATH.read_text(encoding="utf-8")) if SAMPLES_PATH.exists() else {}
    checks = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok, detail))

    def run(case_key: str) -> dict:
        return triage(cases[case_key], projects, atoms)

    # AC-001：信息完整时命中 ≥2 项，每项 ≥1 条条款，材料非空
    a = run("complete")
    hits = [i for i in a["items"] if i["conclusion"] in HIT]
    check("AC-001 命中≥2 项", len(hits) >= 2, f"命中 {len(hits)}")
    check("AC-001 每项有条款", all(i["reasons"] for i in hits), "")
    check("AC-001 材料清单非空", all(i["materials"] for i in hits), "")

    # AC-002：缺困难认定 → 该项目的需核实且指明缺项
    b = run("missing_income")
    gz = next(i for i in b["items"] if i["project_id"] == "GZ")
    check("AC-002 缺字段→需核实", gz["conclusion"] == "need_check", gz["conclusion"])
    check("AC-002 指明缺项", "income_difficulty" in gz["missing_fields"], str(gz["missing_fields"]))

    # AC-003：全部不满足 → 无命中且给渠道
    c = run("no_match")
    check("AC-003 无命中", c["hit_count"] == 0, f"命中 {c['hit_count']}")
    check("AC-003 给咨询渠道", bool(c["fallback"]), c["fallback"])

    # I-1：任何结论至少 1 条依据
    check("I-1 无条款不出结论", all(i["reasons"] for i in a["items"] if i["conclusion"]), "")

    # I-2：结论措辞不出"符合／不符合"（政策原文片段允许出现"符合条件"等原文表述）
    texts = []
    for res in (a, b, c):
        for i in res["items"]:
            texts.append(i["conclusion_label"] or "")
            texts.extend(i.get("notes", []))
    check("I-2 结论措辞不含符合/不符合", all("符合" not in t for t in texts), "")
    check("I-2 结论仅三档", all(
        i["conclusion"] in (None, "eligible", "need_check", "not_applicable") for i in a["items"]
    ), "")

    # D-010：只用现行原子
    check("D-010 排除被替代原子", stats["excluded_superseded"] == 4, str(stats))

    # 评审类不得自动判为可申请
    lz = next(i for i in a["items"] if i["project_id"] == "LZ")
    check("评审类恒需核实", lz["conclusion"] == "need_check" and lz["review_required"], lz["conclusion"])

    width = max(len(n) for n, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} 通过；原子 {stats['current']} 条现行、{stats['excluded_superseded']} 条被替代")
    return 0 if failed == 0 else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="R-003 确定性判等")
    parser.add_argument("--input", help="学生情况 JSON（单对象或 {case: 对象} 集合）")
    parser.add_argument("--case", default="", help="input 为集合时指定 case 名；默认逐个跑")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()
    if not args.input:
        parser.print_help()
        return 2
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    projects = load_projects()
    atoms, _ = load_atoms()
    if args.case:
        results = {args.case: triage(payload[args.case], projects, atoms)}
    elif all(isinstance(v, dict) and "stage" in v for v in payload.values()) and "stage" not in payload:
        results = {k: triage(v, projects, atoms) for k, v in payload.items()}
    else:
        results = {"single": triage(payload, projects, atoms)}
    print(json.dumps(results, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
