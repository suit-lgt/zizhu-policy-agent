"""Private, minimal conversation snapshots; transactions never span model calls."""
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from . import db, qa, qa_context

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_session (
 id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, topic TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS qa_owner ON qa_session(owner_user_id,updated_at);
CREATE TABLE IF NOT EXISTS qa_turn (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
 parent_turn_id TEXT, sanitized_question TEXT NOT NULL, raw_question TEXT NOT NULL DEFAULT '', context_json TEXT NOT NULL,
 evidence_json TEXT NOT NULL DEFAULT '{}', panel_json TEXT NOT NULL DEFAULT '{}',
 validated_answer TEXT NOT NULL DEFAULT '', citations_json TEXT NOT NULL DEFAULT '[]',
 task_json TEXT, state TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'offline',
 fallback_reason_code TEXT, prompt_version TEXT NOT NULL DEFAULT 'qa_workbench_v2',
 created_at TEXT NOT NULL, completed_at TEXT, lease_until TEXT,
 UNIQUE(session_id,client_request_id));
CREATE INDEX IF NOT EXISTS qa_turn_session ON qa_turn(session_id,created_at);
CREATE TABLE IF NOT EXISTS qa_handoff (
 id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, source_turn_id TEXT NOT NULL,
 context_json TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_task_id TEXT);
"""
TERMINAL={'completed','clarified','refused','failed','cancelled','interrupted'}
ACTIVE=('accepted','preparing','validating')
LEASE_SECONDS=120

def _lease(after:int=LEASE_SECONDS)->str:
    return (datetime.now(timezone.utc)+timedelta(seconds=after)).isoformat(timespec='seconds')

def init_schema():
    with closing(db.conn()) as c:
        c.executescript(SCHEMA)
        # 旧库兼容：原始提问字段是后补的，缺列时直接补，不改其他数据。
        cols = {r['name'] for r in c.execute('PRAGMA table_info(qa_turn)')}
        if 'raw_question' not in cols:
            c.execute("ALTER TABLE qa_turn ADD COLUMN raw_question TEXT NOT NULL DEFAULT ''")
        c.commit()

def _session(c,owner,sid):
    row=c.execute('SELECT * FROM qa_session WHERE id=? AND owner_user_id=?',(sid,owner)).fetchone()
    if not row: raise HTTPException(404,'咨询记录不存在或无权访问')
    return dict(row)

def unpack(row):
    d=dict(row)
    for col,key,default in [('context_json','context',{}),('evidence_json','evidence',{}),('panel_json','panel',{}),('citations_json','citations',[]),('task_json','task',None)]:
        d[key]=json.loads(d.pop(col) or 'null') or default
    d['answer']=d.pop('validated_answer'); d['question']=d.pop('sanitized_question')
    d['raw_question']=d.pop('raw_question','') or ''
    d.pop('lease_until',None)
    return d

def recover():
    # A lease avoids interrupting another live worker when four entry services start separately.
    with closing(db.conn()) as c:
        c.execute("UPDATE qa_turn SET state='interrupted',completed_at=?,fallback_reason_code='execution_interrupted' WHERE state IN ('accepted','preparing','validating') AND lease_until<?",(db.now(),db.now()))
        c.commit()

def create(owner):
    now=db.now(); sid=uuid.uuid4().hex
    with closing(db.conn()) as c:
        c.execute('INSERT INTO qa_session VALUES(?,?,?,?,?)',(sid,owner,'新的政策咨询',now,now)); c.commit()
    return {'id':sid,'topic':'新的政策咨询','created_at':now,'updated_at':now}

def listing(owner):
    with closing(db.conn()) as c:
        return [dict(r) for r in c.execute('SELECT id,topic,created_at,updated_at FROM qa_session WHERE owner_user_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 100',(owner,))]

def get(owner,sid):
    with closing(db.conn()) as c:
        out=_session(c,owner,sid)
        out['turns']=[unpack(r) for r in c.execute('SELECT * FROM qa_turn WHERE session_id=? ORDER BY rowid',(sid,))]
    out['current_version']=qa.current_version(db.APP_DB)
    return out

def get_turn(owner,tid):
    with closing(db.conn()) as c:
        row=c.execute('SELECT t.* FROM qa_turn t JOIN qa_session s ON t.session_id=s.id WHERE t.id=? AND s.owner_user_id=?',(tid,owner)).fetchone()
    if not row: raise HTTPException(404,'咨询轮次不存在或无权访问')
    return unpack(row)

def task_snapshot(c,owner,tid,codes):
    row=c.execute('SELECT task_id,owner,policy_version,result_json,status,created_at FROM triage_task WHERE task_id=? AND owner=?',(tid,owner)).fetchone()
    if not row: raise HTTPException(404,'本人任务不存在')
    result=json.loads(row['result_json']); items=[]
    for it in result.get('items',[]):
        if it.get('project_id') not in qa_context.PROJECTS or (codes and it['project_id'] not in codes): continue
        conclusion=it.get('conclusion')
        if conclusion not in ('eligible','need_check','not_applicable',None): continue
        items.append({'project_id':it['project_id'],'project_name':qa_context.PROJECTS[it['project_id']],
                      'conclusion':conclusion,'conclusion_label':{'eligible':'可申请','need_check':'需核实','not_applicable':'暂不适用',None:'未匹配'}[conclusion],
                      'missing_fields':it.get('missing_fields',[]),'review_required':bool(it.get('review_required')),
                      'reasons':[{'clause_id':x['clause_id']} for x in it.get('reasons',[]) if 'clause_id' in x]})
    appeals=[{'status':x['status']} for x in c.execute('SELECT status FROM appeal WHERE task_id=? ORDER BY id',(tid,))]
    return {'task_id':tid,'policy_version':row['policy_version'],'status':row['status'],'created_at':row['created_at'],'items':items,'appeals':appeals}

def _clean_raw(raw):
    """原始提问是用户原文，只做空白修剪和长度上限，不做语义重写。"""
    if raw is None: raw = ''
    if not isinstance(raw,str) or len(raw.strip())>1200: raise HTTPException(422,'原始提问须为不超过 1200 字的文本')
    return raw.strip()


def accept(owner,sid,payload):
    question=payload.get('question'); rid=payload.get('client_request_id')
    if not isinstance(question,str) or not 2<=len(question.strip())<=1200: raise HTTPException(422,'问题长度须为 2–1200 字')
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',rid): raise HTTPException(422,'需要有效的请求标识')
    raw=_clean_raw(payload.get('raw_question'))
    with closing(db.conn()) as c:
        c.execute('BEGIN IMMEDIATE')
        _session(c,owner,sid)
        old=c.execute('SELECT * FROM qa_turn WHERE session_id=? AND client_request_id=?',(sid,rid)).fetchone()
        if old:
            # 幂等重试：同一请求不重复建轮；旧客户端漏传原文时在这次补写。
            if raw and not old['raw_question']:
                c.execute('UPDATE qa_turn SET raw_question=? WHERE id=?',(raw,old['id'])); c.commit()
                old=c.execute('SELECT * FROM qa_turn WHERE id=?',(old['id'],)).fetchone()
            return unpack(old),False
        tail=c.execute('SELECT * FROM qa_turn WHERE session_id=? ORDER BY rowid DESC LIMIT 1',(sid,)).fetchone()
        if tail and tail['state'] in ACTIVE: raise HTTPException(409,'上一轮仍在处理中，请先恢复或取消')
        parent=payload.get('parent_turn_id')
        if parent and (not tail or tail['id']!=parent): raise HTTPException(409,'上下文已更新，请刷新后继续')
        previous=json.loads(tail['context_json']) if tail else {}
        try: ctx=qa_context.parse(question,previous,payload.get('confirmed_context'))
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from None
        ctx['policy_version']=qa.current_version(db.APP_DB) if payload.get('use_current_version') or not previous.get('policy_version') else previous['policy_version']
        tid=payload.get('task_id')
        if not tid and ctx['intent']=='review_progress' and not payload.get('use_current_version'): tid=previous.get('task_id')
        task=task_snapshot(c,owner,tid,ctx['project_codes']) if tid else None
        if task:
            ctx['task_id']=tid; ctx['policy_version']=task['policy_version']; ctx['intent']='review_progress'; ctx['branch']=None
        elif ctx['intent']=='review_progress': ctx['branch']='clarify'
        turnid=uuid.uuid4().hex; now=db.now()
        lease=_lease()
        c.execute('INSERT INTO qa_turn(id,session_id,client_request_id,parent_turn_id,sanitized_question,raw_question,context_json,task_json,state,created_at,lease_until) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (turnid,sid,rid,tail['id'] if tail else None,ctx['sanitized_question'],raw,json.dumps(ctx,ensure_ascii=False),json.dumps(task,ensure_ascii=False),'accepted',now,lease))
        topic='、'.join(qa_context.PROJECTS[x] for x in ctx['project_codes']) or '政策咨询'
        c.execute('UPDATE qa_session SET updated_at=?,topic=? WHERE id=?',(now,topic,sid)); c.commit()
        row=c.execute('SELECT * FROM qa_turn WHERE id=?',(turnid,)).fetchone()
    db.audit(owner,'student','qa_turn_accepted',turnid,{'intent':ctx['intent'],'version':ctx['policy_version']})
    return unpack(row),True

def claim(tid):
    """Worker takes ownership: accepted -> preparing, with a fresh lease."""
    with closing(db.conn()) as c:
        changed=c.execute("UPDATE qa_turn SET state='preparing',lease_until=? WHERE id=? AND state='accepted'",(_lease(),tid)).rowcount
        c.commit()
    return bool(changed)

def renew(tid):
    with closing(db.conn()) as c:
        changed=c.execute("UPDATE qa_turn SET lease_until=? WHERE id=? AND state IN ('accepted','preparing','validating')",(_lease(),tid)).rowcount
        c.commit()
    return bool(changed)

def is_active(tid):
    with closing(db.conn()) as c:
        row=c.execute('SELECT state FROM qa_turn WHERE id=?',(tid,)).fetchone()
    return bool(row and row['state'] in ACTIVE)

def prepare(tid,evidence,panel):
    with closing(db.conn()) as c:
        c.execute("UPDATE qa_turn SET evidence_json=?,panel_json=?,state='validating',lease_until=? WHERE id=? AND state IN ('accepted','preparing')",(json.dumps(evidence,ensure_ascii=False),json.dumps(panel,ensure_ascii=False),_lease(),tid)); c.commit()

def finish(tid,state,answer='',citations=None,mode='offline',fallback=None):
    if state not in TERMINAL: raise ValueError('invalid terminal state')
    with closing(db.conn()) as c:
        changed=c.execute("UPDATE qa_turn SET state=?,validated_answer=?,citations_json=?,mode=?,fallback_reason_code=?,completed_at=? WHERE id=? AND state IN ('accepted','preparing','validating')",
                          (state,answer,json.dumps(citations or [],ensure_ascii=False),mode,fallback,db.now(),tid)).rowcount
        c.commit()
    return bool(changed)

def update_raw(owner,tid,raw):
    """修改已保存的原始提问；值未变时不写库、不记审计，避免重复写入。"""
    raw=_clean_raw(raw)
    if len(raw)<2: raise HTTPException(422,'原始提问须为 2–1200 字')
    with closing(db.conn()) as c:
        changed=c.execute('UPDATE qa_turn SET raw_question=? WHERE id=? AND raw_question<>?'
                          ' AND session_id IN (SELECT id FROM qa_session WHERE owner_user_id=?)',
                          (raw,tid,raw,owner)).rowcount
        c.commit()
        own=c.execute('SELECT t.id FROM qa_turn t JOIN qa_session s ON t.session_id=s.id'
                      ' WHERE t.id=? AND s.owner_user_id=?',(tid,owner)).fetchone()
    if not own: raise HTTPException(404,'咨询轮次不存在或无权访问')
    if changed: db.audit(owner,'student','qa_turn_raw_updated',tid,{'length':len(raw)})
    return get_turn(owner,tid)

def delete(owner,sid):
    with closing(db.conn()) as c:
        c.execute('BEGIN IMMEDIATE'); _session(c,owner,sid)
        c.execute('DELETE FROM qa_handoff WHERE source_turn_id IN (SELECT id FROM qa_turn WHERE session_id=?)',(sid,))
        c.execute('DELETE FROM qa_turn WHERE session_id=?',(sid,)); c.execute('DELETE FROM qa_session WHERE id=?',(sid,)); c.commit()
    db.audit(owner,'student','qa_session_deleted',sid)


def create_handoff(owner, turn_id, kind='triage', task_id=None):
    """Create a short-lived, non-sensitive link to the triage form or own task.

    It never contains student answers and it never creates a task.  The form still
    requires the student to review every field and provide consent.
    """
    if kind not in ('triage', 'progress'):
        raise HTTPException(422, '引导类型不支持')
    turn = get_turn(owner, turn_id)
    if turn['state'] not in ('completed', 'clarified', 'refused'):
        raise HTTPException(409, '请等待本轮咨询结束后再进入初判')
    if kind == 'progress':
        if not isinstance(task_id, str) or not task_id:
            raise HTTPException(422, '需要有效的初判任务编号')
        with closing(db.conn()) as c:
            owned = c.execute('SELECT 1 FROM triage_task WHERE task_id=? AND COALESCE(owner,?)=?',
                              (task_id, owner, owner)).fetchone()
        if not owned:
            raise HTTPException(404, '本人任务不存在')
    project_codes = turn['context'].get('project_codes') or []
    payload = {'source_turn_id': turn_id, 'project_codes': project_codes,
               'policy_version': turn['context'].get('policy_version'),
               'kind': kind, 'task_id': task_id if kind == 'progress' else None}
    handoff_id = uuid.uuid4().hex
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec='seconds')
    with closing(db.conn()) as c:
        c.execute('INSERT INTO qa_handoff(id,owner_user_id,source_turn_id,context_json,expires_at) VALUES(?,?,?,?,?)',
                  (handoff_id, owner, turn_id, json.dumps(payload, ensure_ascii=False), expires))
        c.commit()
    db.audit(owner, 'student', 'qa_handoff_created', handoff_id,
             {'source_turn_id': turn_id, 'projects': project_codes, 'kind': kind})
    return {'id': handoff_id, 'expires_at': expires, 'project_codes': project_codes,
            'kind': kind, 'task_id': payload['task_id']}


def read_handoff(owner, handoff_id):
    with closing(db.conn()) as c:
        row = c.execute('SELECT * FROM qa_handoff WHERE id=? AND owner_user_id=?',
                        (handoff_id, owner)).fetchone()
    if not row or row['expires_at'] < db.now():
        raise HTTPException(404, '初判引导已失效，请从咨询记录重新进入')
    data = json.loads(row['context_json'])
    return {'id': row['id'], 'expires_at': row['expires_at'],
            'kind': data.get('kind', 'triage'),
            'project_codes': data.get('project_codes', []),
            'policy_version': data.get('policy_version'),
            'task_id': data.get('task_id'),
            'consumed_task_id': row['consumed_task_id'],
            'source_turn_id': data.get('source_turn_id')}


def consume_handoff(owner, handoff_id, task_id):
    """Atomically bind a triage handoff to the task it was used for."""
    with closing(db.conn()) as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM qa_handoff WHERE id=? AND owner_user_id=?',
                        (handoff_id, owner)).fetchone()
        if not row or row['expires_at'] < db.now():
            c.rollback(); raise HTTPException(409, '初判引导已失效，请从咨询记录重新进入')
        data = json.loads(row['context_json'])
        if data.get('kind', 'triage') != 'triage':
            c.rollback(); raise HTTPException(409, '该引导不用于创建初判')
        if row['consumed_task_id'] == task_id:
            c.commit(); return {'id': handoff_id, 'reused': True}
        if row['consumed_task_id']:
            c.rollback(); raise HTTPException(409, '该引导已使用，请从咨询记录重新进入')
        changed = c.execute('UPDATE qa_handoff SET consumed_task_id=? WHERE id=? AND consumed_task_id IS NULL',
                            (task_id, handoff_id)).rowcount
        c.commit()
    if not changed:
        raise HTTPException(409, '该引导已使用，请从咨询记录重新进入')
    db.audit(owner, 'student', 'qa_handoff_consumed', handoff_id, {'task_id': task_id})
    return {'id': handoff_id, 'reused': False}
