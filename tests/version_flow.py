"""版本回滚内容验证：发布新版本后检索命中新条款；回滚旧版本后同一问题应拒答。

端口 8036，tempdir 库副本，不写真实库。
"""
import base64, json, os, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

root = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp()); shutil.copy(root/"data"/"app.db", tmp/"app.db"); shutil.copy(root/"data"/"staging"/"policies.db", tmp/"policies.db")
c = sqlite3.connect(tmp/"app.db"); old = c.execute("SELECT version_id FROM policy_version WHERE status='current'").fetchone()[0]; c.close()
env = dict(os.environ, APP_DB=str(tmp/"app.db"), POLICY_DB=str(tmp/"policies.db"), LOG_LEVEL="WARNING", UPLOAD_DIR=str(tmp/"uploads"))
p = subprocess.Popen([sys.executable,"-X","utf8","-m","uvicorn","backend.app.main:app","--host","127.0.0.1","--port","8036"], cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
BASE="http://127.0.0.1:8036"
for _ in range(40):
    try: urllib.request.urlopen(BASE+"/api/health",timeout=2); break
    except Exception: time.sleep(0.5)
H={"X-Role":"admin","X-User":"admin_demo"}
ok=True
def call(m,path,body=None,headers=None):
    d=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(BASE+path,data=d,method=m); r.add_header("Content-Type","application/json")
    for k,v in (headers or {}).items(): r.add_header(k,v)
    try:
        with urllib.request.urlopen(r,timeout=20) as x: return x.status,x.read().decode()
    except urllib.error.HTTPError as e: return e.code,e.read().decode()
def chk(n,c,e=""):
    global ok
    print(("[PASS] " if c else "[FAIL] ")+n,e); ok = ok and c
txt = ("第一条 为测试版本回滚，设立量子助学金专项。\n"
       "第二条 量子助学金用于资助家庭经济困难且参与量子实践的学生。\n"
       "第三条 量子助学金按学年申请，具体标准由学校确定。\n") * 2
code, raw = call("POST","/api/policy/upload",{"filename":"版本回滚测试办法.txt","content_b64":base64.b64encode(txt.encode()).decode()},H)
up=json.loads(raw); chk("上传草稿", code==200 and up.get("doc_id"), str(code))
code, _ = call("POST", f"/api/policy/{up['doc_id']}/verify", {"doc_no":"测试回滚〔2026〕2号","issuer":"测试机关","effective_from":"2026-02-01"}, H)
chk("核文", code==200, str(code))
code, raw = call("POST", f"/api/policy/{up['doc_id']}/split", headers=H); chk("切块", code==200 and json.loads(raw)["clauses"]>=3, raw[:50])
q = "量子"
code, raw = call("POST","/api/qa",{"question":q},headers={"X-Role":"student","X-User":"student_demo"})
before=json.loads(raw); chk("发布前新条款不可检索", code==200 and before.get("refused"), f"refused={before.get('refused')}")
code, raw = call("POST", f"/api/policy/{up['doc_id']}/publish", headers=H)
pub=json.loads(raw); chk("发布新版本并写快照", code==200 and pub.get("snapshot",{}).get("clauses",0)>0, raw[:80])
newv=pub["version_id"]
code, raw = call("POST","/api/qa",{"question":q},headers={"X-Role":"student","X-User":"student_demo"})
after=json.loads(raw); chk("发布后可检索新条款", code==200 and not after.get("refused") and len(after.get("hits") or [])>=1, f"hits={len(after.get('hits') or [])} v={after.get('version_id')}")
chk("回答版本为新版本", after.get("version_id")==newv, f"{after.get('version_id')} vs {newv}")
code, raw = call("POST","/api/policy/rollback",{"version_id":old},H); chk("回滚旧版本", code==200, raw[:60])
code, raw = call("POST","/api/qa",{"question":q},headers={"X-Role":"student","X-User":"student_demo"})
back=json.loads(raw); chk("回滚后新条款不可检索", code==200 and back.get("refused") and len(back.get("hits") or [])==0, f"refused={back.get('refused')}")
chk("回滚后回答版本为旧版", back.get("version_id")==old, f"{back.get('version_id')} vs {old}")
code, raw = call("POST","/api/triage",{"stage":"高职专科","enrollment":"全日制在校","normal_program":True,"grade":2,"income_difficulty":"已认定困难","special_hardship":"特别困难","veteran":False,"orphan":True,"martyr_child":False,"disabled":False,"hukou_province":"广东","academic":"一般","other_provincial_aid":False,"consent":True},headers={"X-Role":"student","X-User":"student_demo"})
tr=json.loads(raw); chk("回滚后初判仍用旧版本", code==200 and tr.get("policy_version")==old and len(tr.get("items") or [])>0, f"{tr.get('policy_version')}")
p.terminate()
try: p.wait(timeout=6)
except Exception: p.kill()
print("ALL", ok)
sys.exit(0 if ok else 1)
