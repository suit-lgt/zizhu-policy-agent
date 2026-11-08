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
 parent_turn_id TEXT, sanitized_question TEXT NOT NULL, context_json TEXT NOT NULL,
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

def init_schema():
    with closing(db.conn()) as c: c.executescript(SCHEMA); c.commit()

def _session(c,owner,sid):
    row=c.execute('SELECT * FROM qa_session WHERE id=? AND owner_user_id=?',(sid,owner)).fetchone()
    if not row: raise HTTPException(404,'咨询记录不存在或无权访问')
    return dict(row)

def unpack(row):
    d=dict(row)
    for col,key,default in [('context_json','context',{}),('evidence_json','evidence',{}),('panel_json','panel',{}),('citations_json','citations',[]),('task_json','task',None)]:
        d[key]=json.loads(d.pop(col) or 'null') or default
    d['answer']=d.pop('validated_answer'); d['question']=d.pop('sanitized_question')
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
    recover()
    with closing(db.conn()) as c:
        return [dict(r) for r in c.execute('SELECT id,topic,created_at,updated_at FROM qa_session WHERE owner_user_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 100',(owner,))]

def get(owner,sid):
    recover()
    with closing(db.conn()) as c:
        out=_session(c,owner,sid)
        out['turns']=[unpack(r) for r in c.execute('SELECT * FROM qa_turn WHERE session_id=? ORDER BY rowid',(sid,))]
    out['current_version']=qa.current_version(db.APP_DB)
    return out

def get_turn(owner,tid):
    recover()
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

def accept(owner,sid,payload):
    question=payload.get('question'); rid=payload.get('client_request_id')
    if not isinstance(question,str) or not 2<=len(question.strip())<=1200: raise HTTPException(422,'问题长度须为 2–1200 字')
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',rid): raise HTTPException(422,'需要有效的请求标识')
    with closing(db.conn()) as c:
        c.execute('BEGIN IMMEDIATE')
        _session(c,owner,sid)
        old=c.execute('SELECT * FROM qa_turn WHERE session_id=? AND client_request_id=?',(sid,rid)).fetchone()
        if old: return unpack(old),False
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
        lease=(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(timespec='seconds')
        c.execute('INSERT INTO qa_turn(id,session_id,client_request_id,parent_turn_id,sanitized_question,context_json,task_json,state,created_at,lease_until) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (turnid,sid,rid,tail['id'] if tail else None,ctx['sanitized_question'],json.dumps(ctx,ensure_ascii=False),json.dumps(task,ensure_ascii=False),'accepted',now,lease))
        topic='、'.join(qa_context.PROJECTS[x] for x in ctx['project_codes']) or '政策咨询'
        c.execute('UPDATE qa_session SET updated_at=?,topic=? WHERE id=?',(now,topic,sid)); c.commit()
        row=c.execute('SELECT * FROM qa_turn WHERE id=?',(turnid,)).fetchone()
    db.audit(owner,'student','qa_turn_accepted',turnid,{'intent':ctx['intent'],'version':ctx['policy_version']})
    return unpack(row),True

def prepare(tid,evidence,panel):
    with closing(db.conn()) as c:
        c.execute("UPDATE qa_turn SET evidence_json=?,panel_json=?,state='validating' WHERE id=? AND state IN ('accepted','preparing')",(json.dumps(evidence,ensure_ascii=False),json.dumps(panel,ensure_ascii=False),tid)); c.commit()

def finish(tid,state,answer='',citations=None,mode='offline',fallback=None):
    if state not in TERMINAL: raise ValueError('invalid terminal state')
    with closing(db.conn()) as c:
        changed=c.execute("UPDATE qa_turn SET state=?,validated_answer=?,citations_json=?,mode=?,fallback_reason_code=?,completed_at=? WHERE id=? AND state IN ('accepted','preparing','validating')",
                          (state,answer,json.dumps(citations or [],ensure_ascii=False),mode,fallback,db.now(),tid)).rowcount
        c.commit()
    return bool(changed)

def delete(owner,sid):
    with closing(db.conn()) as c:
        c.execute('BEGIN IMMEDIATE'); _session(c,owner,sid)
        c.execute('DELETE FROM qa_handoff WHERE source_turn_id IN (SELECT id FROM qa_turn WHERE session_id=?)',(sid,))
        c.execute('DELETE FROM qa_turn WHERE session_id=?',(sid,)); c.execute('DELETE FROM qa_session WHERE id=?',(sid,)); c.commit()
    db.audit(owner,'student','qa_session_deleted',sid)


def create_handoff(owner, turn_id):
    """Create a short-lived, non-sensitive link to the existing triage form.

    It never contains student answers and it never creates a task.  The form still
    requires the student to review every field and provide consent.
    """
    turn = get_turn(owner, turn_id)
    if turn['state'] not in ('completed', 'clarified', 'refused'):
        raise HTTPException(409, '请等待本轮咨询结束后再进入初判')
    project_codes = turn['context'].get('project_codes') or []
    payload = {'source_turn_id': turn_id, 'project_codes': project_codes,
               'policy_version': turn['context'].get('policy_version')}
    handoff_id = uuid.uuid4().hex
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec='seconds')
    with closing(db.conn()) as c:
        c.execute('INSERT INTO qa_handoff(id,owner_user_id,source_turn_id,context_json,expires_at) VALUES(?,?,?,?,?)',
                  (handoff_id, owner, turn_id, json.dumps(payload, ensure_ascii=False), expires))
        c.commit()
    db.audit(owner, 'student', 'qa_handoff_created', handoff_id,
             {'source_turn_id': turn_id, 'projects': project_codes})
    return {'id': handoff_id, 'expires_at': expires, 'project_codes': project_codes}


def read_handoff(owner, handoff_id):
    with closing(db.conn()) as c:
        row = c.execute('SELECT * FROM qa_handoff WHERE id=? AND owner_user_id=?',
                        (handoff_id, owner)).fetchone()
    if not row or row['expires_at'] < db.now():
        raise HTTPException(404, '初判引导已失效，请从咨询记录重新进入')
    data = json.loads(row['context_json'])
    return {'id': row['id'], 'expires_at': row['expires_at'],
            'project_codes': data.get('project_codes', []),
            'policy_version': data.get('policy_version'),
            'source_turn_id': data.get('source_turn_id')}
