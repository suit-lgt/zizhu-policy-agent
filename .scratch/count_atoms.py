import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "data" / "staging" / "policies.db"
conn = sqlite3.connect(db)
print(conn.execute("SELECT role,determinable,COUNT(*) FROM policy_atom GROUP BY role,determinable").fetchall())
print(conn.execute("SELECT COUNT(*) FROM policy_atom WHERE role='资格' AND determinable='可判等'").fetchone())
print(conn.execute("SELECT COUNT(*) FROM policy_atom WHERE superseded=1").fetchone())
