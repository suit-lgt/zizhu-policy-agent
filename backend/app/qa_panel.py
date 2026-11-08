"""Pure, intent-specific view model. No database access and no model-generated cards."""
from copy import deepcopy
from .qa_context import INTENTS, PROJECTS

def build(bundle):
    b=deepcopy(bundle); ctx=b['context']
    panel={'title':INTENTS[ctx['intent']], 'projects':[PROJECTS[c] for c in ctx['project_codes']],
           'version':ctx.get('policy_version'),'scope_status':b['scope_status'],
           'warnings':b['warnings'],'evidence':b['policy_items'],'actions':b['actions']}
    if ctx.get('branch')=='clarify': panel['choices']=[{'code':c,'name':n} for c,n in PROJECTS.items()]
    if ctx['intent']=='materials': panel['materials']=b['materials']
    if ctx['intent']=='process': panel['steps']=b['procedure']
    if b.get('rule_result'):
        panel['task']=b['rule_result']; panel['missing_fields']=b['missing_fields']
    return panel
