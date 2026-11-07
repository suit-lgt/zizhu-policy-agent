"""页面与入库联检：登录页/后台新页、6 代理 trace、本地上传→核文→切块。

端口 8035，tempdir 库副本，不写真实库。
"""
import base64, json, os, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path
root = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp()); shutil.copy(root/"data"/"app.db", tmp/"app.db"); shutil.copy(root/"data"/"staging"/"policies.db", tmp/"policies.db")
env = dict(os.environ, APP_DB=str(tmp/"app.db"), POLICY_DB=str(tmp/"policies.db"), LOG_LEVEL="WARNING", UPLOAD_DIR=str(tmp/"uploads"))
p = subprocess.Popen([sys.executable,"-X","utf8","-m","uvicorn","backend.app.main:app","--host","127.0.0.1","--port","8035"], cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
BASE="http://127.0.0.1:8035"
for _ in range(40):
    try: urllib.request.urlopen(BASE+"/api/health",timeout=2); break
    except Exception: time.sleep(0.5)
def call(m,path,body=None,headers=None,raw=False):
    d=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(BASE+path,data=d,method=m); r.add_header("Content-Type","application/json")
    for k,v in (headers or {}).items(): r.add_header(k,v)
    try:
        with urllib.request.urlopen(r,timeout=25) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
H={"X-Role":"admin","X-User":"admin_demo"}
ok=True
def chk(name,cond,extra=""):
    global ok
    print(("[PASS] " if cond else "[FAIL] ")+name, extra)
    ok = ok and cond

code, html = call("GET","/login.html"); chk("登录页可取", code==200 and "注册新账号" in html and "注册四步说明" in html, f"{code}")
code, html = call("GET","/admin.html"); chk("后台含审核与入库页", code==200 and "注册审核" in html and "上传并入库" in html and 'id="tab-registrations"' in html and '<a href="/">学生端</a>' not in html)
code, html = call("GET","/"); chk("学生页含协同面板且无跨端入口", code==200 and "多智能体协同过程" in html and ("登录/注册" in html or "请先登录或注册" in html) and "/admin.html" not in html and "/review.html" not in html)
body={"stage":"高职专科","enrollment":"全日制在校","normal_program":True,"grade":2,"income_difficulty":"已认定困难","special_hardship":"特别困难","veteran":False,"orphan":True,"martyr_child":False,"disabled":False,"hukou_province":"广东","academic":"一般","other_provincial_aid":False,"consent":True}
code, raw = call("POST","/api/triage",body,headers={"X-Role":"student","X-User":"student_demo"})
data=json.loads(raw) if code==200 else {}
chk("初判返回多智能体", code==200 and len(data.get("trace") or [])>=6 and (data.get("agents") or {}).get("count")==6, f"{code} trace={len(data.get('trace') or [])}")
chk("含学生摘要", bool(data.get("student_summary")), (data.get("student_summary") or "")[:40])
txt = "第一条 为规范学生资助资金管理，制定本办法。\n第二条 国家助学金用于资助家庭经济困难学生。\n第三条 申请学生应如实填报家庭经济情况。\n" + "第四条 本办法自发布之日起施行。"*3
code, raw = call("POST","/api/policy/upload",{"filename":"测试办法.txt","content_b64":base64.b64encode(txt.encode()).decode()},H)
up=json.loads(raw) if code==200 else {}
chk("本地上传入库", code==200 and up.get("doc_id") and up.get("status")=="pending_verify", f"{code} {raw[:80]}")
code, _ = call("POST","/api/policy/upload",{"filename":"测试办法.txt","content_b64":base64.b64encode(txt.encode()).decode()},H)
chk("重复上传去重", code==409, str(code))
code, _ = call("POST",f"/api/policy/{up.get('doc_id')}/split",headers=H)
chk("未核文不得切块", code==400, str(code))
code, raw = call("POST",f"/api/policy/{up.get('doc_id')}/verify",{"doc_no":"测试〔2026〕1号","issuer":"测试机关","effective_from":"2026-01-01"},H)
chk("核文通过", code==200, f"{code} {raw[:60]}")
code, raw = call("POST",f"/api/policy/{up.get('doc_id')}/split",headers=H)
chk("核文后可切块", code==200 and json.loads(raw).get("clauses",0)>=3, f"{code} {raw[:80]}")
p.terminate()
try: p.wait(timeout=6)
except Exception: p.kill()
print("ALL", ok)
sys.exit(0 if ok else 1)
