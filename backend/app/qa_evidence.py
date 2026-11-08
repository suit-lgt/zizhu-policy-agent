"""Version-bound evidence shared by conversation, panel and exports. No lazy snapshot writes."""
import json
import re
import sqlite3
from datetime import date
from contextlib import closing
from . import db, qa

META_DDL = """CREATE TABLE IF NOT EXISTS policy_snapshot_meta (
 version_id TEXT NOT NULL, doc_id INTEGER NOT NULL, metadata_json TEXT NOT NULL,
 PRIMARY KEY(version_id,doc_id));"""

def published(version):
    with closing(db.conn()) as c:
        return bool(c.execute("SELECT 1 FROM policy_version WHERE version_id=? AND status IN ('current','archived')",(version,)).fetchone())

def snapshot_metadata(version, policy_db=None):
    """Called ONLY at new publication. Never backfill historical metadata from mutable drafts."""
    with closing(qa.conn(policy_db)) as c:
        c.executescript(META_DDL)
        for row in c.execute("SELECT * FROM policy_staging WHERE status IN ('verified','published')").fetchall():
            d=dict(row)
            meta={k:d.get(k) for k in ('issuer','effective_from','effective_to','layer')}
            # Layer is not jurisdiction. Existing schema does not assert scope or open-ended validity.
            meta.update({'region':None,'education_stage':None,'validity_confirmed':False,'scope_confirmed':False})
            c.execute('INSERT OR IGNORE INTO policy_snapshot_meta VALUES(?,?,?)',(version,d['id'],json.dumps(meta,ensure_ascii=False)))
        c.commit()

def resolve(version, clause_id, policy_db=None):
    if not version or not published(version): raise LookupError('该政策版本不可引用')
    with closing(qa.conn(policy_db)) as c:
        try:
            row=c.execute('SELECT * FROM policy_snapshot_clause WHERE version_id=? AND clause_id=?',(version,clause_id)).fetchone()
        except sqlite3.OperationalError: row=None
        if not row: raise LookupError('该版本没有此条款快照')
        d=dict(row)
        try:
            m=c.execute('SELECT metadata_json FROM policy_snapshot_meta WHERE version_id=? AND doc_id=?',(version,d['doc_id'])).fetchone()
        except sqlite3.OperationalError: m=None
    meta=json.loads(m[0]) if m else {}
    complete=bool(meta.get('scope_confirmed') and meta.get('validity_confirmed') and meta.get('effective_from'))
    today=date.today().isoformat()
    valid=complete and meta['effective_from']<=today and (not meta.get('effective_to') or meta['effective_to']>=today)
    return {'evidence_id':f"{version}:{clause_id}",'version_id':version,'clause_id':clause_id,'doc_id':d['doc_id'],
            'doc_no':d.get('doc_no') or '', 'policy_title':d.get('title') or '', 'article_no':d.get('clause_no') or '',
            'quote':d['text'],'char_start':d.get('char_start'),'char_end':d.get('char_end'),'url':d.get('url') or '',
            'effective_from':meta.get('effective_from'),'effective_to':meta.get('effective_to'),'metadata':meta,
            'scope_status':'supported' if valid else 'needs_verification'}

def retrieve(question, context, policy_db=None, limit=5, legacy=False):
    version=context.get('policy_version')
    if not version or not published(version): return []
    if legacy:
        hits=qa.find_hits(question,limit,policy_db,version)
        return [resolve(version,h['clause_id'],policy_db) for h in hits]
    codes=context.get('project_codes',[])
    with closing(qa.conn(policy_db)) as c:
        try:
            rows=c.execute('SELECT clause_id,text FROM policy_snapshot_clause WHERE version_id=?',(version,)).fetchall()
            atoms=c.execute('SELECT * FROM policy_snapshot_atom WHERE version_id=? AND COALESCE(superseded,0)=0',(version,)).fetchall()
        except sqlite3.OperationalError: return []
    linked={a['clause_id'] for a in atoms if a['project_code'] in codes}
    standards={a['clause_id'] for a in atoms if a['project_code'] in codes and a['field']=='standard_amount'}
    from .qa_context import ALIASES
    words=[w for code in codes for w in ALIASES.get(code,())]
    terms={'materials':('材料','证明','申请表'), 'process':('申请','办理','时间','提交','评审'),
           'benefit_standard':('标准','元','叠加','同时'), 'policy_scope':('条件','对象','学生','全日制')}.get(context['intent'],())
    scores=[]
    for row in rows:
        text=row['text']; cid=row['clause_id']
        strong=cid in linked or any(w in text for w in words)
        if not codes: strong=len(question)>=4 and question in text
        if not strong: continue
        score=10*(cid in linked)+sum(w in text for w in words)+sum(w in text for w in terms)
        if context['intent']=='benefit_standard' and cid in standards: score+=20
        score+=sum(3 for w in context.get('topics',[]) if w in text)
        scores.append((score,cid))
    return [resolve(version,cid,policy_db) for _,cid in sorted(scores,key=lambda x:(-x[0],x[1]))[:limit]]

def build(question, context, policy_db=None, task=None):
    items=[] if context.get('branch') else retrieve(question,context,policy_db)
    if task:
        # Task reason IDs must resolve in the task's frozen version, never the current clause table.
        ids={r.get('clause_id') for it in task['items'] for r in it.get('reasons',[])}
        for cid in ids:
            try:
                item=resolve(context['policy_version'],cid,policy_db)
                if not any(x['evidence_id']==item['evidence_id'] for x in items): items.append(item)
            except LookupError: pass
    materials=[]
    if context['intent']=='materials' and context['project_codes']:
        with closing(db.conn()) as c:
            for code in context['project_codes']:
                for row in c.execute('SELECT * FROM material_item WHERE project_id=? ORDER BY id',(code,)):
                    d=dict(row)
                    source=next((i for i in items if i['clause_id']==d['source_clause_id'] and d['name'] in i['quote']),None)
                    verified=d['source_type']=='政策条款' and source is not None
                    materials.append({'name':d['name'],'project_code':code,'source_type':'政策明文' if verified else '待学校确认',
                                      'required':bool(d['required']) if verified else False,'confirmation':'verified' if verified else 'pending',
                                      'evidence_id':source['evidence_id'] if verified else None,
                                      'note':'请以本校当前办理通知核对；通用清单不等于政策必需材料' if not verified else '见引用原文'})
    warnings=[]
    state='supported' if items and all(i['scope_status']=='supported' for i in items) else ('needs_verification' if items else 'no_evidence')
    if items and state=='needs_verification': warnings.append('本版适用范围或有效期信息尚不完整。以下为原文参考，不能据此确认当前个人资格或实际发放金额。')
    if context.get('task_id'): warnings.append('这是本人历史任务快照，不代表当前申请建议；重新初判需核对信息并授权。')
    if context.get('personal_guidance'): warnings.append('聊天中的个人情况不会写入初判资料，请在表单中核对并确认。')
    missing=[]
    if task:
        fields={f['code']:f for f in json.loads((db.ROOT/'data/fields.json').read_text(encoding='utf-8'))['fields']}
        for it in task['items']:
            for key in it.get('missing_fields',[]):
                if key in fields and not any(x['code']==key for x in missing):
                    missing.append({'code':key,'name':fields[key]['name'],'source':fields[key]['source']})
    actions=[{'id':'triage','label':'核对情况，进入初判'},{'id':'progress','label':'查看本人办理进度'}]
    return {'context':context,'policy_items':items,'rule_result':task,'materials':materials,'missing_fields':missing,
            'scope_status':state,'warnings':warnings,'actions':actions,
            'procedure':([{'title':'核对情况与授权','description':'进入初判表单，确认学籍与认定信息。'},
                          {'title':'核对学校要求','description':'办理渠道、截止时间和材料以本校最新通知为准。'},
                          {'title':'查看进度与复核入口','description':'已有初判可在办理进度页继续操作；提交前仍需本人确认。'}] if context['intent']=='process' else [])}
