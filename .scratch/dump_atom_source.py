import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KW = re.compile(
    r"申请|条件|认定|困难|资助对象|享受|标准|全日制|学籍|户籍|收入|残疾|低保|特困|"
    r"建档立卡|孤儿|烈士|贷款|奖学金|助学金|免学费"
)

conn = sqlite3.connect(ROOT / "data" / "staging" / "policies.db")
out = []
for doc_id in (32, 28, 27):
    rows = conn.execute(
        "SELECT id,seq,clause_no,clause_type,char_count,text FROM policy_clause "
        "WHERE doc_id=? AND clause_type IN ('条','段落','前言') ORDER BY seq",
        (doc_id,),
    ).fetchall()
    hit = [r for r in rows if KW.search(r[5])]
    out.append(f"### doc {doc_id}  条款 {len(rows)} 命中 {len(hit)}\n")
    for r in hit:
        head = "[c%d | seq%d | %s | %s | %d字]" % (r[0], r[1], r[2] or "-", r[3], r[4])
        out.append(head + "\n" + r[5] + "\n")
conn.close()

path = ROOT / ".scratch" / "atom_source.md"
path.write_text("\n".join(out), encoding="utf-8")
print("written", path, sum(len(x) for x in out))
