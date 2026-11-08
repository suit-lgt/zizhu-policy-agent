"""Private consultation API. Cookie authentication only; execution independent of SSE delivery."""
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from . import accounts, db, provider, qa, qa_evidence, qa_panel, qa_sessions as sessions

router=APIRouter(prefix='/api/qa')
_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='policy-consult')
_slots=threading.BoundedSemaphore(8)

def private_user(request:Request):
    user=accounts.session_user(request.cookies.get('fund_session'))
    if not user: raise HTTPException(401,'请先登录后查看本人咨询')
    if user['role']!='student': raise HTTPException(403,'此入口仅供学生本人使用')
    return user

def work(owner,tid):
    try:
        turn=sessions.get_turn(owner,tid)
        if turn['state'] in sessions.TERMINAL: return
        evidence=qa_evidence.build(turn['question'],turn['context'],task=turn['task'])
        panel=qa_panel.build(evidence)
        sessions.prepare(tid,evidence,panel)
        result=provider.compose_workbench(evidence)
        if sessions.finish(tid,result['state'],result['text'],result['citations'],result['mode'],result['fallback_reason_code']):
            db.audit(owner,'student','qa_turn_finished',tid,{'state':result['state'],'mode':result['mode']})
    except Exception:
        sessions.finish(tid,'failed',fallback='execution_failed')
    finally: _slots.release()

@router.get('/options')
def options(u=Depends(private_user)):
    from .qa_context import PROJECTS
    return {'projects':[{'code':c,'name':n} for c,n in PROJECTS.items()], 'current_version':qa.current_version(db.APP_DB)}

@router.post('/sessions')
def create(u=Depends(private_user)): return sessions.create(u['user'])

@router.get('/sessions')
def listing(u=Depends(private_user)): return {'sessions':sessions.listing(u['user'])}

@router.get('/sessions/{sid}')
def detail(sid:str,u=Depends(private_user)): return sessions.get(u['user'],sid)

@router.delete('/sessions/{sid}')
def delete(sid:str,u=Depends(private_user)):
    sessions.delete(u['user'],sid); return {'deleted':True}

@router.post('/sessions/{sid}/turns')
def accept(sid:str,payload:dict=Body(...),u=Depends(private_user)):
    sessions.recover()
    turn,fresh=sessions.accept(u['user'],sid,payload)
    if fresh:
        if not _slots.acquire(blocking=False):
            sessions.finish(turn['id'],'failed',fallback='capacity_busy')
        else:
            try: _pool.submit(work,u['user'],turn['id'])
            except Exception:
                _slots.release();sessions.finish(turn['id'],'failed',fallback='execution_failed')
    return {'turn_id':turn['id'],'state':turn['state'],'created':fresh}

@router.get('/turns/{tid}')
def get_turn(tid:str,u=Depends(private_user)): return sessions.get_turn(u['user'],tid)

@router.post('/turns/{tid}/cancel')
def cancel(tid:str,u=Depends(private_user)):
    sessions.get_turn(u['user'],tid);sessions.finish(tid,'cancelled')
    return sessions.get_turn(u['user'],tid)


@router.post('/turns/{tid}/handoff')
def handoff(tid:str, u=Depends(private_user)):
    return sessions.create_handoff(u['user'], tid)


@router.get('/handoffs/{handoff_id}')
def get_handoff(handoff_id:str, u=Depends(private_user)):
    return sessions.read_handoff(u['user'], handoff_id)

@router.get('/turns/{tid}/evidence/{eid}')
def evidence(tid:str,eid:str,u=Depends(private_user)):
    turn=sessions.get_turn(u['user'],tid)
    item=next((x for x in turn['evidence'].get('policy_items',[]) if x['evidence_id']==eid),None)
    if not item: raise HTTPException(404,'本轮未引用此证据')
    # Return the original saved evidence, also when immutable storage is unavailable later.
    return item


def _export_markdown(session: dict, turns: list[dict]) -> str:
    lines = ['# 本人政策咨询记录', '',
             f"- 导出范围：{'单轮' if len(turns) == 1 else '全会话'}",
             '- 说明：本记录仅作政策理解与办理准备参考，最终以资助部门认定为准。', '']
    for number, turn in enumerate(turns, 1):
        context, panel = turn['context'], turn['panel']
        lines += [f'## {number}. {turn["question"]}', '',
                  f"- 处理状态：{turn['state']}",
                  f"- 回答模式：{turn['mode']}",
                  f"- 政策版本：{context.get('policy_version') or '未匹配'}", '',
                  '### 说明', turn['answer'] or '本轮没有可导出的完整说明。', '']
        if panel.get('evidence'):
            lines += ['### 政策依据']
            for item in panel['evidence']:
                lines.append(f"- 《{item['doc_no']}》{item['article_no']}（版本 {item['version_id']}）：{item['quote']}")
            lines.append('')
        if panel.get('materials'):
            lines += ['### 材料准备']
            for item in panel['materials']:
                suffix = '原文明确要求' if item.get('required') else '待学校确认'
                lines.append(f"- {item['name']}：{item['source_type']}，{suffix}")
            lines.append('')
        if panel.get('warnings'):
            lines += ['### 注意事项'] + [f'- {item}' for item in panel['warnings']] + ['']
    return '\n'.join(lines)


@router.get('/sessions/{sid}/export')
def export_session(sid:str, turn_id:str|None=None, scope:str|None=None, u=Depends(private_user)):
    session = sessions.get(u['user'], sid)
    if turn_id:
        turns = [turn for turn in session['turns'] if turn['id'] == turn_id]
        if len(turns) != 1:
            raise HTTPException(404, '该轮咨询不存在或不属于此会话')
    elif scope == 'all':
        turns = session['turns']
    else:
        raise HTTPException(422, '请明确导出单轮或全部会话')
    content = _export_markdown(session, turns)
    db.audit(u['user'], 'student', 'qa_export', sid,
             {'scope': 'turn' if turn_id else 'all', 'turn_count': len(turns)})
    return Response(content, media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="policy-consultation.md"',
                             'Cache-Control': 'no-store'})

def event(name,data): return f'event: {name}\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n'

@router.get('/turns/{tid}/events')
def events(tid:str,request:Request,u=Depends(private_user)):
    sessions.get_turn(u['user'],tid)
    async def generate():
        sent=False;started=time.monotonic()
        while True:
            if await request.is_disconnected(): return
            try: turn=await asyncio.to_thread(sessions.get_turn,u['user'],tid)
            except HTTPException:
                yield event('error',{'code':'record_unavailable'});return
            if turn['panel'] and not sent:
                yield event('meta',{'turn_id':tid,'panel':turn['panel'],'context':turn['context']});sent=True
            if turn['state'] in sessions.TERMINAL:
                if turn['state'] in ('completed','clarified','refused'):
                    for i in range(0,len(turn['answer']),36):
                        yield event('chunk',{'text':turn['answer'][i:i+36]})
                    yield event('citations',turn['citations'])
                yield event('done',{'turn_id':tid,'status':turn['state'],'mode':turn['mode'],'fallback_reason_code':turn['fallback_reason_code']});return
            if time.monotonic()-started>125:
                yield event('error',{'code':'stream_timeout'});return
            yield ': waiting\n\n'
            await asyncio.sleep(.25)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no'})
