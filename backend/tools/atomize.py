"""原子条件抽取与校验（R-002 首批：3 份核心文件）。

原则（对应 AGENTS.md I-1/I-2/I-3/I-7 与 docs/data-contract.md）：
- 每条原子条件必须有原文片段（quote），并在对应条款内逐字定位；定位失败即不导入。
- 字段只取 data/fields.json 里的最小集（D-009），不临时造字段。
- 金额类（role=标准）只作展示，不参与资格判等；多份文件口径冲突时按版本取最新、旧的标 superseded。
- 导入的原子全部 pending_verify、publishable=0，文号未核前不得对外。

用法：
  python -X utf8 tools/atomize.py import --file data/atoms/core3_atoms.json
  python -X utf8 tools/atomize.py verify
  python -X utf8 tools/atomize.py coverage
  python -X utf8 tools/atomize.py export
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policy_collector as pc  # noqa: E402

ROOT = pc.ROOT
DATA_DIR = pc.DATA_DIR
FIELDS_PATH = ROOT / "data" / "fields.json"
ATOM_DIR = ROOT / "data" / "atoms"

BOOL_FIELDS = {"true", "false"}
ALLOWED_OPS = {"=", ">=", ">", "<=", "<", "依据", "区间"}


def connect(db_path=None) -> sqlite3.Connection:
    conn = pc.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_atom (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            atom_id TEXT NOT NULL UNIQUE,
            project_code TEXT NOT NULL,
            project_name TEXT,
            doc_id INTEGER NOT NULL,
            doc_no TEXT,
            clause_id INTEGER,
            clause_no TEXT,
            field TEXT NOT NULL,
            op TEXT NOT NULL,
            value TEXT NOT NULL,
            quote TEXT NOT NULL,
            quote_start INTEGER NOT NULL,
            quote_end INTEGER NOT NULL,
            role TEXT NOT NULL,
            determinable TEXT,
            superseded INTEGER NOT NULL DEFAULT 0,
            version TEXT,
            status TEXT NOT NULL DEFAULT 'pending_verify',
            publishable INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_atom_project ON policy_atom(project_code)")
    conn.commit()
    return conn


def load_fields() -> dict:
    payload = json.loads(FIELDS_PATH.read_text(encoding="utf-8"))
    return {f["code"]: f for f in payload["fields"]}


def doc_rows(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT id,doc_no,title,body_text,effective_from,publish_date,layer FROM policy_staging"
    ).fetchall()
    return {
        (r[1] or ""): {
            "doc_id": r[0],
            "doc_no": r[1],
            "title": r[2],
            "body": r[3] or "",
            "effective": r[4] if r[4] and r[4] != "pending_verify" else "",
            "publish": r[5] if r[5] and r[5] != "pending_verify" else "",
            "layer": r[6],
        }
        for r in rows
    }


def clause_row(conn: sqlite3.Connection, clause_id: int) -> dict | None:
    r = conn.execute(
        "SELECT id,doc_id,seq,clause_no,clause_type,text,char_start,char_end FROM policy_clause WHERE id=?",
        (clause_id,),
    ).fetchone()
    if not r:
        return None
    return {
        "clause_id": r[0],
        "doc_id": r[1],
        "seq": r[2],
        "clause_no": r[3],
        "type": r[4],
        "text": r[5],
        "start": r[6],
        "end": r[7],
    }


def version_of(doc: dict) -> str:
    return doc["effective"] or doc["publish"] or "pending_verify"


def locate_in_clause(clause_text: str, quote: str, occurrence: int) -> int | None:
    start = 0
    for _ in range(occurrence + 1):
        idx = clause_text.find(quote, start)
        if idx < 0:
            return None
        last = idx
        start = idx + 1
    return last


def validate_atom(atom: dict, docs: dict, fields: dict, conn: sqlite3.Connection) -> tuple[dict | None, str | None]:
    required = ("atom_id", "project_code", "doc_no", "clause_id", "field", "op", "value", "quote", "role")
    missing = [k for k in required if not atom.get(k)]
    if missing:
        return None, f"{atom.get('atom_id', '?')} 缺字段：{','.join(missing)}"
    doc = docs.get(atom["doc_no"])
    if not doc:
        return None, f"{atom['atom_id']} 文号未在暂存库：{atom['doc_no']}（先采集入库）"
    clause = clause_row(conn, int(atom["clause_id"]))
    if not clause:
        return None, f"{atom['atom_id']} 条款不存在：{atom['clause_id']}"
    if clause["doc_id"] != doc["doc_id"]:
        return None, f"{atom['atom_id']} 条款与文号不属同一份文件"
    fdef = fields.get(atom["field"])
    if not fdef:
        return None, f"{atom['atom_id']} 字段不在字典：{atom['field']}"
    value = str(atom["value"])
    # 定义为"依据某文件"、或"区间"类标准原子，其取值不是字段枚举值
    if atom["op"] not in ("依据", "区间"):
        if fdef["type"] == "bool" and value not in BOOL_FIELDS:
            return None, f"{atom['atom_id']} 布尔字段取值非法：{value}"
        if fdef["type"] == "enum" and "values" in fdef and value not in fdef["values"]:
            return None, f"{atom['atom_id']} 取值不在枚举：{value}（{atom['field']}）"
        if fdef["type"] == "int" and not value.isdigit():
            return None, f"{atom['atom_id']} 整数取值非法：{value}"
    if atom["op"] not in ALLOWED_OPS:
        return None, f"{atom['atom_id']} 运算符不支持：{atom['op']}"
    occurrence = int(atom.get("occurrence", 0))
    offset = locate_in_clause(clause["text"], atom["quote"], occurrence)
    if offset is None:
        return None, f"{atom['atom_id']} 原文片段在条款 {clause['clause_no'] or clause['clause_id']} 内定位失败"
    quote_start = clause["start"] + offset
    quote_end = quote_start + len(atom["quote"])
    body = doc["body"]
    if body[quote_start:quote_end] != atom["quote"]:
        return None, f"{atom['atom_id']} 片段与正文偏移不一致（数据可能已更新，重跑 split）"
    return {
        "atom_id": atom["atom_id"],
        "project_code": atom["project_code"],
        "project_name": atom.get("project_name", atom["project_code"]),
        "doc_id": doc["doc_id"],
        "doc_no": doc["doc_no"],
        "clause_id": clause["clause_id"],
        "clause_no": clause["clause_no"] or "",
        "field": atom["field"],
        "op": atom["op"],
        "value": value,
        "quote": atom["quote"],
        "quote_start": quote_start,
        "quote_end": quote_end,
        "role": atom["role"],
        "determinable": atom.get("determinable", ""),
        "version": version_of(doc),
        "note": atom.get("note", ""),
    }, None


def import_atoms(files: list[Path]) -> dict:
    fields = load_fields()
    conn = connect()
    docs = doc_rows(conn)
    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    errors: list[str] = []
    rows: list[dict] = []
    for payload in payloads:
        for atom in payload.get("atoms", []):
            row, err = validate_atom(atom, docs, fields, conn)
            if err:
                errors.append(err)
            else:
                rows.append(row)
    if errors:
        conn.close()
        return {"imported": 0, "errors": errors, "note": "校验失败，未写入"}

    conn.execute("DELETE FROM policy_atom")
    for row in rows:
        conn.execute(
            """INSERT INTO policy_atom
               (atom_id,project_code,project_name,doc_id,doc_no,clause_id,clause_no,field,op,value,
                quote,quote_start,quote_end,role,determinable,superseded,version,status,publishable,note,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,'pending_verify',0,?,?)""",
            (
                row["atom_id"],
                row["project_code"],
                row["project_name"],
                row["doc_id"],
                row["doc_no"],
                row["clause_id"],
                row["clause_no"],
                row["field"],
                row["op"],
                row["value"],
                row["quote"],
                row["quote_start"],
                row["quote_end"],
                row["role"],
                row["determinable"],
                row["version"],
                row["note"],
                pc.now_utc(),
            ),
        )
    conn.commit()
    superseded = resolve_standards(conn)
    conn.close()
    return {
        "imported": len(rows),
        "files": [p.name for p in files],
        "superseded": superseded,
        "errors": [],
    }


def _sort_date(text: str) -> str:
    return text if text and text[:4].isdigit() else "0000-00-00"


def resolve_standards(conn: sqlite3.Connection) -> int:
    """同一项目的标准类原子里，按文件生效/发布日期取最新，旧的标 superseded。

    政策版本锚定（D-005）的最小实现：不删除旧值，保留可追溯记录。
    """
    count = 0
    projects = [r[0] for r in conn.execute(
        "SELECT DISTINCT project_code FROM policy_atom WHERE role='标准'"
    ).fetchall()]
    for project in projects:
        rows = conn.execute(
            "SELECT atom_id,value,version,doc_no FROM policy_atom WHERE project_code=? AND role='标准'",
            (project,),
        ).fetchall()
        newest_date = max(_sort_date(r[2]) for r in rows)
        newest = [r for r in rows if _sort_date(r[2]) == newest_date]
        for atom_id, value, version, doc_no in rows:
            # 同一版本内的多条标准（如"平均 3700"与"区间 2500-5000"）互为补充，不算替代
            if _sort_date(version) == newest_date:
                continue
            note_add = f"被 {newest[0][3]}（{newest_date} 起）替代，保留备查"
            conn.execute(
                "UPDATE policy_atom SET superseded=1, note=CASE WHEN note IS NULL OR note='' THEN ? ELSE note||'；'||? END"
                " WHERE atom_id=?",
                (note_add, note_add, atom_id),
            )
            count += 1
    conn.commit()
    return count


def verify() -> dict:
    conn = connect()
    docs = doc_rows(conn)
    bad = []
    checked = 0
    for atom_id, doc_id, clause_id, qs, qe, quote, clause_start, clause_end in conn.execute(
        "SELECT a.atom_id,a.doc_id,a.clause_id,a.quote_start,a.quote_end,a.quote,"
        "c.char_start,c.char_end FROM policy_atom a LEFT JOIN policy_clause c ON c.id=a.clause_id"
    ):
        checked += 1
        doc = conn.execute("SELECT body_text FROM policy_staging WHERE id=?", (doc_id,)).fetchone()
        body = (doc[0] if doc else "") or ""
        if body[qs:qe] != quote:
            bad.append({"atom_id": atom_id, "reason": "正文偏移不一致"})
            continue
        if clause_start is None or not (clause_start <= qs and qe <= clause_end):
            bad.append({"atom_id": atom_id, "reason": "片段越出所属条款范围"})
    conn.close()
    return {"checked": checked, "bad": bad, "ok": checked - len(bad)}


QUALIFY_KW = ("申请", "资助对象", "奖励对象", "条件", "认定", "家庭经济困难", "学籍", "年级", "全日制")
# 与"高职学生本人"这一首版主用户无关的条款类型：职责分工、预算分配、其他学段、程序性规定
SCOPE_OUT_KW = (
    "职责", "预算", "分配", "绩效", "经费", "分担", "施行", "废止",
    "学前", "义务教育", "普通高中", "中等职业", "中职",
    "实施细则", "档案", "信息系统", "宣传", "公示", "奖补资金", "设立", "用于本地区",
)


def coverage() -> dict:
    conn = connect()
    per_project = conn.execute(
        "SELECT project_code,project_name,COUNT(*),SUM(role='资格'),SUM(role='标准'),"
        "SUM(role IN ('评审','排除','定义')),SUM(superseded) FROM policy_atom GROUP BY project_code"
    ).fetchall()
    per_doc = []
    for doc_id, doc_no, total, covered in conn.execute(
        "SELECT p.id,p.doc_no,"
        "(SELECT COUNT(*) FROM policy_clause c WHERE c.doc_id=p.id AND c.clause_type IN ('条','段落')) AS total,"
        "(SELECT COUNT(DISTINCT a.clause_id) FROM policy_atom a WHERE a.doc_id=p.id) AS covered "
        "FROM policy_staging p WHERE p.id IN (SELECT DISTINCT doc_id FROM policy_atom)"
    ).fetchall():
        # 有资格类关键词但没有原子的条款：可能是漏抽，需人工看
        clauses = conn.execute(
            "SELECT id,clause_no,text FROM policy_clause WHERE doc_id=? AND clause_type IN ('条','段落')",
            (doc_id,),
        ).fetchall()
        atom_clauses = {r[0] for r in conn.execute(
            "SELECT DISTINCT clause_id FROM policy_atom WHERE doc_id=?", (doc_id,)
        ).fetchall()}
        misses = []
        for c in clauses:
            if c[0] in atom_clauses or not any(k in c[2] for k in QUALIFY_KW):
                continue
            scope_out = any(k in c[2] for k in SCOPE_OUT_KW)
            misses.append(
                {
                    "clause_id": c[0],
                    "clause_no": c[1],
                    "preview": c[2][:28],
                    "scope_out": scope_out,
                }
            )
        per_doc.append(
            {
                "doc_no": doc_no,
                "clauses": total,
                "covered": covered,
                "scope_out": sum(1 for m in misses if m["scope_out"]),
                "real_miss": sum(1 for m in misses if not m["scope_out"]),
                "misses": misses,
            }
        )
    conn.close()
    return {
        "by_project": [
            {
                "project_code": r[0],
                "project_name": r[1],
                "atoms": r[2],
                "qualify": r[3],
                "standard": r[4],
                "review_exclude_def": r[5],
                "superseded": r[6],
            }
            for r in per_project
        ],
        "by_doc": per_doc,
    }


def export(out_path: Path | None = None) -> Path:
    out_path = out_path or (ATOM_DIR / "atom_index.md")
    conn = connect()
    rows = conn.execute(
        "SELECT atom_id,project_name,field,op,value,doc_no,clause_no,role,determinable,superseded,"
        "quote,note FROM policy_atom ORDER BY project_code,atom_id"
    ).fetchall()
    conn.close()
    lines = [
        "# 原子条件清单（草稿）",
        "",
        f"导出时间：{pc.now_utc()}；共 {len(rows)} 条；全部 `pending_verify`、`publishable=0`，政策文号未核前不得对外。",
        "",
        "| 原子ID | 项目 | 字段 | 判定 | 值 | 依据 | 类型 | 可判程度 | 已替代 | 原文片段 | 备注 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for atom_id, project, field, op, value, doc_no, clause_no, role, det, sup, quote, note in rows:
        cells = [
            atom_id, project or "", field, op, value, f"{doc_no} {clause_no}".strip(), role, det or "",
            "是" if sup else "否", quote.replace("|", "／").replace("\n", " "), (note or "").replace("|", "／"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="原子条件导入、校验、覆盖与导出")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("import", help="导入原子条件文件（重建表）")
    p1.add_argument("--file", nargs="+", required=True)
    sub.add_parser("verify", help="校验每条原子的原文片段能否定位")
    sub.add_parser("coverage", help="按项目/文件看覆盖，并列出可能漏抽的条款")
    p3 = sub.add_parser("regress", help="字段字典变更后的全量回归：导入＋校验＋覆盖＋字典对照")
    p3.add_argument("--file", nargs="+", required=True)
    p2 = sub.add_parser("export", help="导出原子条件清单")
    p2.add_argument("--out", default="")
    args = parser.parse_args()

    if args.cmd == "import":
        files = [Path(f) for f in args.file]
        missing = [str(f) for f in files if not f.exists()]
        if missing:
            print(json.dumps({"error": f"文件不存在：{missing}"}, ensure_ascii=False))
            return 2
        print(json.dumps(import_atoms(files), ensure_ascii=False))
        return 0
    if args.cmd == "verify":
        result = verify()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not result["bad"] else 1
    if args.cmd == "coverage":
        print(json.dumps(coverage(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "regress":
        files = [Path(f) for f in args.file]
        missing = [str(f) for f in files if not f.exists()]
        if missing:
            print(json.dumps({"error": f"文件不存在：{missing}"}, ensure_ascii=False))
            return 2
        imp = import_atoms(files)
        if imp["imported"] == 0:
            print(json.dumps(imp, ensure_ascii=False))
            return 1
        ver = verify()
        cov = coverage()
        payload = json.loads(FIELDS_PATH.read_text(encoding="utf-8"))
        used = {r[0] for r in connect().execute("SELECT DISTINCT field FROM policy_atom").fetchall()}
        unused = [f["code"] for f in payload["fields"] if f["code"] not in used]
        print(json.dumps({
            "import": {"imported": imp["imported"], "superseded": imp["superseded"], "errors": imp["errors"]},
            "verify": {"checked": ver["checked"], "ok": ver["ok"], "bad": ver["bad"]},
            "coverage": {
                "by_project": [{k: p[k] for k in ("project_code", "atoms", "qualify", "standard", "superseded")} for p in cov["by_project"]],
                "by_doc": [{"doc_no": d["doc_no"], "covered": d["covered"], "scope_out": d["scope_out"], "real_miss": d["real_miss"]} for d in cov["by_doc"]],
            },
            "dict": {"version": payload["version"], "fields": len(payload["fields"]), "unused": unused},
        }, ensure_ascii=False, indent=2))
        return 0 if not ver["bad"] else 1
    if args.cmd == "export":
        print(str(export(Path(args.out) if args.out else None)))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
