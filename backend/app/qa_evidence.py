"""Version-bound evidence shared by conversation, panel and exports. No lazy snapshot writes."""
import json
import re
import sqlite3
from datetime import date
from contextlib import closing
from fastapi import HTTPException
from . import db, qa

META_DDL = """CREATE TABLE IF NOT EXISTS policy_snapshot_meta (
 version_id TEXT NOT NULL, doc_id INTEGER NOT NULL, metadata_json TEXT NOT NULL,
 PRIMARY KEY(version_id,doc_id));"""

def published(version):
    with closing(db.conn()) as c:
        return bool(c.execute("SELECT 1 FROM policy_version WHERE version_id=? AND status IN ('current','archived')",(version,)).fetchone())

def _meta_from_verify(app, doc):
    """Merge immutable human verification into snapshot metadata; never invent scope."""
    meta={k:doc.get(k) for k in ('issuer','effective_from','effective_to','layer')}
    meta.update({'region':None,'education_stage':None,'validity_confirmed':False,'scope_confirmed':False})
    row=app.execute('SELECT * FROM policy_verify WHERE doc_id=?',(doc['id'],)).fetchone()
    if row and row['verifier'] and row['verified_at'] and row['issuer'] and row['effective_from'] \
            and (not row['doc_no'] or row['doc_no']==doc.get('doc_no')):
        meta.update({'issuer':row['issuer'],'effective_from':row['effective_from'],
                     'doc_no':row['doc_no'],'verified_by':row['verifier'],
                     'verified_at':row['verified_at'],'validity_confirmed':True})
    return meta


def snapshot_metadata(version, policy_db=None):
    """Seed metadata for one published version from immutable verification records."""
    with closing(qa.conn(policy_db)) as c, closing(db.conn()) as app:
        c.executescript(META_DDL)
        try:
            rows=c.execute("SELECT * FROM policy_staging WHERE status IN ('verified','published')").fetchall()
        except sqlite3.OperationalError:
            rows=[]
        for row in rows:
            d=dict(row)
            # Layer is not jurisdiction. Scope remains unconfirmed until a reviewer confirms it.
            c.execute('INSERT OR IGNORE INTO policy_snapshot_meta VALUES(?,?,?)',
                      (version,d['id'],json.dumps(_meta_from_verify(app,d),ensure_ascii=False)))
        c.commit()


def ensure_meta_for_published():
    """Backfill metadata for versions published before this mechanism existed."""
    with closing(db.conn()) as c:
        versions=[r[0] for r in c.execute("SELECT version_id FROM policy_version WHERE status IN ('current','archived')")]
    for version in versions:
        snapshot_metadata(version)


def confirm_meta(version, doc_id, payload, actor):
    """Reviewer-controlled scope/validity confirmation for a frozen snapshot doc."""
    if not published(version):
        raise HTTPException(404, '该政策版本不可引用')
    if not isinstance(doc_id, int):
        raise HTTPException(422, '需要有效的文件编号')
    for key in ('scope_confirmed','validity_confirmed'):
        if not isinstance(payload.get(key), bool):
            raise HTTPException(422, f'{key} 须为布尔值')
    verifier=str(payload.get('verifier') or '').strip()
    if not verifier:
        raise HTTPException(422, '需要核实人')
    for key in ('effective_from','effective_to'):
        value=payload.get(key)
        if value not in (None,'') and (not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value)):
            raise HTTPException(422, f'{key} 须为 YYYY-MM-DD')
    with closing(qa.conn()) as c:
        c.executescript(META_DDL)
        exists=c.execute('SELECT 1 FROM policy_snapshot_clause WHERE version_id=? AND doc_id=?',(version,doc_id)).fetchone()
        if not exists:
            raise HTTPException(404, '该版本没有此文件快照')
        row=c.execute('SELECT metadata_json FROM policy_snapshot_meta WHERE version_id=? AND doc_id=?',(version,doc_id)).fetchone()
        meta=json.loads(row[0]) if row else {}
        meta.update({k:payload[k] for k in ('scope_confirmed','validity_confirmed')})
        meta.update({'issuer':payload.get('issuer') or meta.get('issuer'),
                     'effective_from':payload.get('effective_from') or meta.get('effective_from'),
                     'effective_to':payload.get('effective_to'),
                     'confirmed_by':verifier,'confirmed_at':db.now()})
        c.execute('INSERT OR REPLACE INTO policy_snapshot_meta VALUES(?,?,?)',
                  (version,doc_id,json.dumps(meta,ensure_ascii=False)))
        c.commit()
    db.audit(actor,'admin','qa_policy_meta_confirmed',f'{version}:{doc_id}',
             {'scope_confirmed':payload['scope_confirmed'],'validity_confirmed':payload['validity_confirmed']})
    return meta

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


def to_factpack(bundle):
    """EvidenceBundle → FactPack：程序把可核验事实整理成模型的唯一输入。纯函数，不落库。

    ADR-0002：模型只组织语言、无权检索或挑选条款。每条 quote 事实带 anchor（version:clause_id）
    与 span 可回点原文；数字、文号、结论三档只出现在 facts 内，模型输入即事实全集。
    """
    ctx = bundle.get('context') or {}
    facts = []
    for i, item in enumerate(bundle.get('policy_items') or []):
        facts.append({
            'id': item.get('evidence_id') or f'q{i}', 'kind': 'quote',
            'text': item.get('quote', ''), 'anchor': item.get('evidence_id'),
            'span': [item.get('char_start'), item.get('char_end')],
            'doc_no': item.get('doc_no', ''), 'article_no': item.get('article_no', ''),
        })
    rule = bundle.get('rule_result')
    if rule:
        for i, it in enumerate(rule.get('items') or []):
            facts.append({
                'id': f'c{i}', 'kind': 'conclusion',
                'project_code': it.get('project_id'), 'project_name': it.get('project_name'),
                'conclusion': it.get('conclusion'), 'conclusion_label': it.get('conclusion_label'),
                'missing_fields': it.get('missing_fields', []),
            })
    for i, m in enumerate(bundle.get('materials') or []):
        facts.append({
            'id': f'm{i}', 'kind': 'material', 'name': m.get('name'),
            'project_code': m.get('project_code'), 'source_type': m.get('source_type'),
            'required': m.get('required'), 'note': m.get('note'),
        })
    for i, p in enumerate(bundle.get('procedure') or []):
        facts.append({'id': f'p{i}', 'kind': 'procedure',
                      'title': p.get('title'), 'description': p.get('description')})
    prohibited = [
        '不得新增任何金额、数字、日期、文号、条款号、机构名',
        '不得改写或新增资格三档结论（eligible/need_check/not_applicable）',
        '不得输出「符合／不符合／通过认定」',
        '不得复述姓名、证件号、联系方式、家庭收入金额',
    ]
    return {'intent': ctx.get('intent'), 'project_codes': ctx.get('project_codes', []),
            'policy_version': ctx.get('policy_version'), 'facts': facts, 'prohibited': prohibited}
