"""三态 Provider：offline（离线规则）／online（OpenAI 兼容）／mock（自测）。

契约见 docs/ai-contract.md：
- offline 为默认模式，不联网、不调模型，用模板把条款拼成说明，功能不缩减；
- online 需 `FUND_LLM_BASE`／`FUND_LLM_KEY`／`FUND_LLM_MODEL`，输出必须过结构校验与业务校验；
  失败重试 1 次，仍失败回退 offline 并记录 last_error；
- 页面与导出件必须标注当前模式；离线模式不得伪装成在线。

自测：`python -X utf8 -m app.provider --selftest`
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "qa_v1.md"
PROMPT_VERSION = "qa_v1"

MODES = ("offline", "online", "mock")
FORBIDDEN = ("不符合", "符合", "通过认定")  # 契约：只出可申请／需核实

_last: dict = {"error": None, "at": None}
_counts = {"calls": 0, "online": 0, "fallbacks": 0, "mock": 0}

OFFLINE_NOTE = "当前为离线规则模式（未调用模型），答案只做条款摘录。"


def mode() -> str:
    m = (os.environ.get("FUND_LLM_MODE") or "offline").strip().lower()
    return m if m in MODES else "offline"


def base_url() -> str:
    return (os.environ.get("FUND_LLM_BASE") or "").rstrip("/")


def model_name() -> str:
    return os.environ.get("FUND_LLM_MODEL") or ""


def configured() -> bool:
    return bool(base_url() and os.environ.get("FUND_LLM_KEY") and model_name())


def status() -> dict:
    return {
        "mode": mode(),
        "requested_mode": os.environ.get("FUND_LLM_MODE") or "offline",
        "configured": configured(),
        "base_configured": bool(base_url()),
        "key_configured": bool(os.environ.get("FUND_LLM_KEY")),
        "model": model_name() or None,
        "prompt_version": PROMPT_VERSION,
        "last_error": _last["error"],
        "last_error_at": _last["at"],
        "count_calls": _counts["calls"],
        "count_online": _counts["online"],
        "count_fallbacks": _counts["fallbacks"],
        "mode_note": OFFLINE_NOTE if mode() == "offline" else "在线模型模式：输出经结构与业务校验，失败回退离线",
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def offline_answer(hits: list[dict]) -> str:
    lines = [f"{i + 1}. 《{h['policy_title']}》（{h['doc_no']}）{h['article_no']}："
             f"{' '.join(h['quote'][:180].split())}"
             for i, h in enumerate(hits)]
    return ("根据以下政策条款：\n" + "\n".join(lines) +
            "\n以上为条款摘录，是否适用于你需结合自身情况由学校认定。" + OFFLINE_NOTE)


def validate(text: str, hits: list[dict]) -> tuple[bool, str]:
    """结构＋业务校验：长度、禁用词、必须提到本次检索到的文号。"""
    if not isinstance(text, str) or not (20 <= len(text.strip()) <= 1200):
        return False, "长度不合规"
    for word in FORBIDDEN:
        if word in text:
            return False, f"出现禁用措辞：{word}"
    doc_nos = [h.get("doc_no") for h in hits if h.get("doc_no")]
    if doc_nos and not any(d in text for d in doc_nos):
        return False, "未引用任何本次检索到的文号"
    return True, "ok"


def _prompt_text() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return f"# {PROMPT_VERSION}\n只依据给定条款作答，禁止判定资格，禁止编造条款号。"


def _call_online(question: str, hits: list[dict], timeout: float) -> str:
    clauses = "\n".join(f"[{h['clause_id']}] 《{h['policy_title']}》（{h['doc_no']}）"
                        f"{h['article_no']}：{h['quote'][:600]}" for h in hits)
    payload = {
        "model": model_name(),
        "temperature": 0,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": _prompt_text()},
            {"role": "user", "content": f"条款：\n{clauses}\n\n学生提问：{question}"},
        ],
    }
    req = urllib.request.Request(
        base_url() + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('FUND_LLM_KEY', '')}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    if content.startswith("{"):
        content = json.loads(content).get("answer", "")
    return content


def compose(question: str, hits: list[dict]) -> dict:
    """返回 {text, mode, model, fallback, error}；离线或失败一律给出可用文案。"""
    _counts["calls"] += 1
    m = mode()
    if m == "mock":
        _counts["mock"] += 1
        return {"text": offline_answer(hits), "mode": "mock", "model": "mock",
                "fallback": False, "error": None}
    if m != "online" or not configured():
        return {"text": offline_answer(hits), "mode": "offline", "model": None,
                "fallback": m == "online", "error": None if m == "offline" else "在线模式缺少配置，已回退离线"}
    timeout = float(os.environ.get("FUND_LLM_TIMEOUT", "20"))
    last_why = ""
    for attempt in range(2):  # 首次 + 重试 1 次
        try:
            text = _call_online(question, hits, timeout)
            ok, why = validate(text, hits)
            if ok:
                _counts["online"] += 1
                return {"text": text, "mode": "online", "model": model_name(),
                        "fallback": False, "error": None}
            last_why = why
        except Exception as exc:  # 网络、鉴权、结构异常都走回退
            last_why = f"{type(exc).__name__}: {exc}"
    _counts["fallbacks"] += 1
    _last.update({"error": last_why or "校验失败", "at": _now()})
    return {"text": offline_answer(hits), "mode": "offline", "model": None,
            "fallback": True, "error": _last["error"]}


# ADR-0002：模型只把程序生成的 FactPack 组织成自然语言，不做证据选择；
# 事实守恒校验保证数字/文号/结论只来自 facts，超集即拒，失败回退离线模板。
WORKBENCH_PROMPT_VERSION = 'qa_workbench_v2'

_NUM_RE = re.compile(r'\d+(?:\.\d+)?')
_DOCNO_RE = re.compile(r'〔\d{4}〕\d+号')
_CONCLUSION_WORDS = ('可申请', '需核实', '暂不适用')
_SENSITIVE_RE = re.compile(r'(1[3-9]\d{9}|\d{17}[\dXx]|\d{16,19})')
# 判定性措辞：I-2 拦截「判定个人」的表述；原文的「符合条件」这类无人称描述不在拦截范围。
_JUDGMENT_RE = re.compile(r'(不符合|通过认定|(?:你|您|我|本人|该生)\s*(?:不)?符合)')


def _fact_numbers(facts):
    nums = set()
    for f in facts:
        nums |= set(_NUM_RE.findall(json.dumps(f, ensure_ascii=False)))
    return nums


def _fact_docnos(facts):
    docnos = set()
    for f in facts:
        docnos |= set(_DOCNO_RE.findall(json.dumps(f, ensure_ascii=False)))
    return docnos


def _fact_conclusion_words(facts):
    words = set()
    for f in facts:
        text = json.dumps(f, ensure_ascii=False)
        if f.get('kind') == 'conclusion' and f.get('conclusion_label'):
            words.add(f['conclusion_label'])
        for w in _CONCLUSION_WORDS:
            if w in text:
                words.add(w)
    return words


def validate_fact_consistency(payload, facts):
    """事实守恒校验：模型输出的事实原子必须 ⊆ 程序 facts。五层，任一失败即拒。"""
    if not isinstance(payload, dict) or set(payload) != {'answer', 'claims'}:
        return False, 'unsupported_claim'
    answer = payload.get('answer'); claims = payload.get('claims')
    if not isinstance(answer, str) or not (1 <= len(answer.strip()) <= 2000):
        return False, 'invalid_answer'
    allowed = {f['id'] for f in facts}
    if not isinstance(claims, list) or not claims or \
            any(not isinstance(c, str) or c not in allowed for c in claims):
        return False, 'invalid_reference'
    if len(claims) != len(set(claims)):
        return False, 'duplicate_reference'
    if not (set(_NUM_RE.findall(answer)) <= _fact_numbers(facts)):
        return False, 'fabricated_number'
    if not (set(_DOCNO_RE.findall(answer)) <= _fact_docnos(facts)):
        return False, 'fabricated_docno'
    for word in _CONCLUSION_WORDS:
        if word in answer and word not in _fact_conclusion_words(facts):
            return False, 'conclusion_mismatch'
    if _JUDGMENT_RE.search(answer):
        return False, 'forbidden_judgment'
    if _SENSITIVE_RE.search(answer):
        return False, 'sensitive_leak'
    return True, 'ok'


def _call_workbench(factpack):
    prompt=(ROOT/'prompts/qa_workbench_v2.md').read_text(encoding='utf-8')
    payload={'model':model_name(),'temperature':0,'max_tokens':2000,'messages':[
        {'role':'system','content':prompt},{'role':'user','content':json.dumps(factpack,ensure_ascii=False)}]}
    req=urllib.request.Request(base_url()+'/chat/completions',data=json.dumps(payload).encode(),
        headers={'Content-Type':'application/json','Authorization':f"Bearer {os.environ.get('FUND_LLM_KEY','')}"},method='POST')
    timeout=max(1,min(float(os.environ.get('FUND_LLM_TIMEOUT','20')),25))
    with urllib.request.urlopen(req,timeout=timeout) as response:
        data=json.loads(response.read(1000000).decode())
    text=data['choices'][0]['message']['content'].strip()
    text=re.sub(r'^```(?:json)?|```$','',text,flags=re.M).strip()
    return json.loads(text)


def compose_workbench(bundle):
    from .qa_context import PROJECTS
    from .qa_evidence import to_factpack
    ctx=bundle['context']; items=bundle['policy_items']
    factpack=to_factpack(bundle)
    requested=mode(); actual='mock' if requested=='mock' else 'offline'; failure=None
    model_text=None
    if requested=='online' and factpack['facts']:
        if not configured(): failure='missing_configuration'
        else:
            for _ in range(2):
                try:
                    payload=_call_workbench(factpack)
                    if validate_fact_consistency(payload,factpack['facts'])[0]:
                        model_text=payload['answer']; actual='online'; failure=None; break
                    failure='validation_failed'
                except Exception: failure='provider_unavailable'
    topic='、'.join(PROJECTS[c] for c in ctx['project_codes']) or '该项目'
    if ctx.get('branch')=='clarify':
        text='请选择要咨询的资助项目，我会沿用这一项目回答后续问题。' if ctx['intent']!='review_progress' else '请先选择一条本人的初判任务，再查看当时的结果与依据。'
        state='clarified'
    elif ctx.get('branch')=='refused' or not items:
        text='当前发布版本没有找到足以回答此问题的政策依据。请确认咨询项目，或联系学校资助中心核实。'
        state='refused'
    else:
        state='completed'
        if model_text is not None:
            text=model_text
        else:
            messages={
                'policy_scope':f'关于{topic}，已整理本版的申请对象与条件原文。请先核对条款，再在初判表单确认本人学籍与认定信息；聊天不会直接判定个人资格。',
                'benefit_standard':f'关于{topic}，请按右侧原文区分资助标准、适用条件和学校具体档位。政策中的平均值或区间不能直接作为个人实际领取金额；学校档位与叠加规则缺少依据时仍需核实。',
                'materials':f'已整理{topic}的办理准备清单。清单逐项标明来源：有原文支持的政策材料可以核对引用，其余通用配置需要学校确认。请勿在聊天中上传证件或账户信息。',
                'process':f'{topic}可按右侧步骤准备：先核对信息，再确认学校要求，已有任务可进入办理进度。学校办理时限与入口未核实前，系统不会替你推定或提交申请。',
                'review_progress':'已调取本人任务快照。右侧显示当时的程序结果、待补信息及当前任务状态；重新初判与提交复核仍需你确认。'}
            text=messages[ctx['intent']]
            if ctx['intent']=='materials' and not bundle['materials']: text='当前没有可核对的本校材料清单。已有政策依据可在右侧查看；具体材料请向学校资助中心确认。'
    # Citations, numbers, status and full quotes only come from the evidence, never model prose.
    citations=[{'evidence_id':h['evidence_id'],'version_id':h['version_id'],'clause_id':h['clause_id'],
                'doc_no':h['doc_no'],'article_no':h['article_no'],'span':[h['char_start'],h['char_end']]}
               for h in items]
    return {'text':text,'citations':citations,'state':state,'mode':actual,'fallback_reason_code':failure,
            'prompt_version':WORKBENCH_PROMPT_VERSION}


def _selftest() -> int:
    hits = [{"clause_id": 351, "article_no": "第十一条", "quote": "国家助学金平均资助标准为每生每年3700元。",
             "policy_title": "广东省学生资助资金管理实施办法", "doc_no": "粤财规〔2026〕1号"}]
    ok = 0
    text = offline_answer(hits)
    ok += 1 if ("粤财规〔2026〕1号" in text and "离线规则模式" in text) else 0
    checks = [
        ("离线文案合规", validate(text, hits)[0] is True),
        ("拒绝判定资格", validate("你符合条件，可以申请", hits)[0] is False),
        ("拒绝编造文号", validate("根据某文件规定，可以申请。", hits)[0] is False),
    ]
    for name, passed in checks:
        print(("PASS" if passed else "FAIL"), name)
        ok += 1 if passed else 0
    os.environ["FUND_LLM_MODE"] = "online"
    os.environ.pop("FUND_LLM_BASE", None)
    res = compose("助学金标准", hits)
    fallback_ok = res["mode"] == "offline" and res["fallback"] is True and "粤财规" in res["text"]
    print(("PASS" if fallback_ok else "FAIL"), "在线缺配置回退离线")
    ok += 1 if fallback_ok else 0
    facts = [
        {'id': 'V:1', 'kind': 'quote', 'text': '国家助学金每年3700元。', 'doc_no': '粤财规〔2026〕1号', 'article_no': '第十一条'},
        {'id': 'c0', 'kind': 'conclusion', 'project_name': '国家助学金', 'conclusion_label': '需核实'},
    ]
    fc = [
        ("事实守恒-合法组织语言", validate_fact_consistency({'answer': '国家助学金标准为每年3700元，本人结论需核实。', 'claims': ['V:1', 'c0']}, facts)[0] is True),
        ("事实守恒-拒编造金额", validate_fact_consistency({'answer': '国家助学金每年99999元。', 'claims': ['V:1']}, facts)[0] is False),
        ("事实守恒-拒编造文号", validate_fact_consistency({'answer': '依据粤财规〔2024〕88号。', 'claims': ['V:1']}, facts)[0] is False),
        ("事实守恒-拒改写结论", validate_fact_consistency({'answer': '你可申请国家助学金。', 'claims': ['V:1']}, facts)[0] is False),
        ("事实守恒-拒不存在引用", validate_fact_consistency({'answer': '标准3700元。', 'claims': ['V:99']}, facts)[0] is False),
        ("事实守恒-不误伤原文符合条件", validate_fact_consistency({'answer': '符合条件的全日制在校生，标准为每年3700元。', 'claims': ['V:1']}, facts)[0] is True),
        ("事实守恒-拒判定措辞你符合", validate_fact_consistency({'answer': '你符合申请条件。', 'claims': ['V:1']}, facts)[0] is False),
    ]
    for name, passed in fc:
        print(("PASS" if passed else "FAIL"), name)
        ok += 1 if passed else 0
    print(f"{ok}/12 PASS")
    return 0 if ok == 12 else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(json.dumps(status(), ensure_ascii=False, indent=2))
