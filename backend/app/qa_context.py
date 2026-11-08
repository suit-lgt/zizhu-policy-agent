"""Bounded, deterministic dialogue context. Raw personal narratives are never persisted."""
import json
from . import db

PROJECTS = {p['code']: p['name'] for p in json.loads(db.PROJECTS.read_text(encoding='utf-8'))['projects']}
ALIASES = {'GZ': ('国家助学金','助学金'), 'LZ': ('国家励志奖学金','励志奖学金'),
           'GJ': ('国家奖学金',), 'XS': ('新生资助','新生补助'), 'XN': ('学费减免','校内资助')}
INTENTS = {'policy_scope':'申请条件', 'benefit_standard':'资助标准', 'materials':'准备材料',
           'process':'办理流程', 'review_progress':'本人办理进度'}
KEYS = {'review_progress': ('进度','复核','申诉','初判结果','需核实','我的任务'),
        'materials': ('材料','证明','清单'), 'benefit_standard': ('多少','金额','标准','叠加','同时享受','多少钱','档位'),
        'process': ('哪里','办理','怎么申请','如何申请','截止','什么时候','时间','流程','渠道')}
TOPICS = ('困难认定','全日制','二年级','退役','学制','评审','叠加','同时享受','截止','档位','成绩')

def parse(question, previous=None, confirmed=None):
    previous = previous or {}
    confirmed = confirmed or {}
    if set(confirmed) - {'project_codes'}:
        raise ValueError('仅支持确认咨询项目；个人情况请在初判表单核对')
    codes = confirmed.get('project_codes')
    if codes is not None and (not isinstance(codes,list) or not codes or len(codes)>3 or any(c not in PROJECTS for c in codes)):
        raise ValueError('咨询项目不在现有项目字典中')
    explicit = [c for c, words in ALIASES.items() if any(w in question for w in words)]
    codes = list(dict.fromkeys(codes or explicit or previous.get('project_codes', [])))
    intent = next((k for k, words in KEYS.items() if any(w in question for w in words)), 'policy_scope')
    if not any(w in question for w in ('条件','范围','资格','申请')) and intent == 'policy_scope' and question.strip() in ('还有呢','继续','详细说说'):
        intent = previous.get('intent','policy_scope')
    personal = any(w in question for w in ('我能','我可以','我符合','我家','父亲','母亲','收入','身份证','手机号','我叫','我是','残疾','孤儿'))
    off = any(w in question for w in ('天气','股票','彩票','星座','游戏','写代码','写论文'))
    topics = [w for w in TOPICS if w in question]
    # The stored/model-facing question is an allowlisted semantic reformulation, not regex-only redaction.
    safe = '、'.join(PROJECTS[c] for c in codes) + '：' + INTENTS[intent]
    if topics: safe += '（'+'、'.join(topics)+'）'
    if personal: safe += '；个人情况请在初判表单确认'
    return {'intent':intent,'project_codes':codes,'context_source':'confirmed' if confirmed else ('question' if explicit else 'previous'),
            'personal_guidance':personal,'topics':topics,'sanitized_question':safe,
            'branch':'refused' if off else ('clarify' if not codes and intent!='review_progress' else None),
            'policy_version':previous.get('policy_version'),'task_id':None}
