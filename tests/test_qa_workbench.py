"""Synthetic fixtures only; never opens live policy/student databases."""
import json, os, sqlite3, tempfile, unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from backend.app import db, qa

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Path(self.tmp.name)/"app.db"
        self.policy = Path(self.tmp.name)/"policies.db"
        self.connections = []
        connect = sqlite3.connect
        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            self.connections.append(connection)
            return connection
        self.connect_patch = patch.object(sqlite3, "connect", side_effect=tracked_connect)
        self.connect_patch.start()
        self.patches = [patch.object(db,"APP_DB",self.app),patch.object(qa,"APP_DB",self.app),patch.object(qa,"POLICY_DB",self.policy)]
        for p in self.patches: p.start()
        db.init_schema()
        with db.conn() as c:
            c.execute("INSERT INTO policy_version(version_id,created_at,status,snapshot_json) VALUES('V1','2026-01-01','current','{}')")
        with sqlite3.connect(self.policy) as c:
            c.executescript(qa.SNAPSHOT_DDL+qa.SNAPSHOT_ATOM_DDL)
            for vid,amount in [('V1',100),('V2',200)]:
                text=f"国家助学金资助标准每年{amount}元。申请对象为全日制本专科学生。"
                c.execute("INSERT INTO policy_snapshot_clause(version_id,clause_id,doc_id,doc_no,title,url,clause_no,text,char_start,char_end) VALUES(?,1,1,'测试文号','测试政策','https://example.gov.cn/p','第一条',?,10,?)",(vid,text,10+len(text)))
                c.execute("INSERT INTO policy_snapshot_atom(version_id,atom_id,project_code,clause_id,field,role,superseded,quote) VALUES(?,'GZ-TEST','GZ',1,'standard_amount','标准',0,?)",(vid,text))
            c.execute("INSERT INTO policy_snapshot_clause(version_id,clause_id,doc_id,text) VALUES('draft',9,9,'国家助学金每年999元')")
            c.execute("CREATE TABLE IF NOT EXISTS policy_staging(id INTEGER PRIMARY KEY,doc_no TEXT,issuer TEXT,"
                      "effective_from TEXT,effective_to TEXT,layer TEXT,status TEXT)")
            c.execute("INSERT INTO policy_staging VALUES(1,'测试文号','测试发布机关','2026-01-01',NULL,'国家','published')")
    def tearDown(self):
        for connection in reversed(self.connections):
            connection.close()
        self.connect_patch.stop()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()
    def test_version_provenance(self):
        from backend.app import qa_evidence as e
        hits=e.retrieve('国家助学金多少钱',{'project_codes':['GZ'],'intent':'benefit_standard','policy_version':'V1'},self.policy)
        self.assertEqual([x['clause_id'] for x in hits],[1])
        self.assertIn('100元',hits[0]['quote'])
        self.assertEqual(hits[0]['char_start'],10)
        self.assertEqual(e.resolve('V1',1,self.policy)['quote'],hits[0]['quote'])
        self.assertEqual(e.retrieve('国家助学金',{'project_codes':['GZ'],'intent':'policy_scope','policy_version':'draft'},self.policy),[])
    def test_weak_match_and_metadata(self):
        from backend.app import qa_evidence as e
        ctx={'project_codes':[],'intent':'policy_scope','policy_version':'V1'}
        self.assertEqual(e.retrieve('量子纠缠实验条件',ctx,self.policy),[])
        item=e.resolve('V1',1,self.policy)
        self.assertEqual(item['scope_status'],'needs_verification')
        self.assertIsNone(item['effective_from'])
        with self.assertRaises(LookupError): e.resolve('draft',9,self.policy)
    def test_materials_are_not_policy_requirements(self):
        from backend.app import qa_evidence as e
        with db.conn() as c:
            c.execute("INSERT INTO material_item(project_id,name,source_type,required) VALUES('GZ','演示材料','学校要求',1)")
        ctx={'project_codes':['GZ'],'intent':'materials','policy_version':'V1'}
        b=e.build('国家助学金需要什么材料',ctx,self.policy)
        self.assertFalse(b['materials'][0]['required'])
        self.assertEqual(b['materials'][0]['confirmation'],'pending')


class SessionTests(EvidenceTests):
    def test_owner_idempotency_and_context(self):
        from backend.app import qa_sessions as s
        session=s.create('alice')
        t,fresh=s.accept('alice',session['id'],{'question':'国家助学金申请条件','client_request_id':'r1'})
        again,new=s.accept('alice',session['id'],{'question':'国家助学金申请条件','client_request_id':'r1'})
        self.assertEqual(t['id'],again['id']); self.assertTrue(fresh); self.assertFalse(new)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException): s.get_turn('bob',t['id'])
        with self.assertRaises(HTTPException): s.accept('alice',session['id'],{'question':'国家奖学金','client_request_id':'r2'})
        s.finish(t['id'],'completed',answer='已保存的条款参考')
        t2,_=s.accept('alice',session['id'],{'question':'要什么材料','client_request_id':'r2','parent_turn_id':t['id']})
        self.assertEqual(t2['context']['project_codes'],['GZ'])
        self.assertEqual(t2['context']['intent'],'materials')
        s.finish(t2['id'],'completed',answer='待学校确认')
        t3,_=s.accept('alice',session['id'],{'question':'国家奖学金多少','client_request_id':'r3','parent_turn_id':t2['id']})
        self.assertEqual(t3['context']['project_codes'],['GJ'])
    def test_minimal_privacy_and_restart(self):
        from backend.app import qa_sessions as s
        session=s.create('alice')
        raw_input='我叫张三，手机号13812345678，父亲收入12345元，国家助学金我能申请吗'
        t,_=s.accept('alice',session['id'],{'question':raw_input,'raw_question':raw_input,'client_request_id':'r1'})
        # 语义提问仍不夹带个人叙述；原文按交付要求单独保存在 raw_question。
        for word in ('张三','13812345678','12345','父亲'): self.assertNotIn(word,t['question'])
        self.assertEqual(t['raw_question'],raw_input)
        self.assertTrue(t['context']['personal_guidance'])
        with db.conn() as c: c.execute("UPDATE qa_turn SET lease_until='2000-01-01'")
        s.recover()
        self.assertEqual(s.get_turn('alice',t['id'])['state'],'interrupted')
        with db.conn() as c:
            c.execute("INSERT INTO review_event(task_id,action,created_at) VALUES('audit-task','submitted','now')")
        s.delete('alice',session['id'])
        with db.conn() as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM review_event').fetchone()[0],1)
    def test_invalid_confirmed_fields(self):
        from backend.app.qa_context import parse
        with self.assertRaises(ValueError): parse('助学金',confirmed={'income':3000})
        with self.assertRaises(ValueError): parse('助学金',confirmed={'project_codes':['UNKNOWN']})
    def test_raw_greeting_reformulated_and_persisted(self):
        from backend.app import qa_context, qa_sessions as s
        raw = '你好'
        parsed = qa_context.parse(raw, {})
        self.assertNotEqual(parsed['sanitized_question'], raw)
        self.assertIn('申请条件', parsed['sanitized_question'])
        session = s.create('alice')
        turn, _ = s.accept('alice', session['id'], {'question': raw, 'raw_question': raw, 'client_request_id': 'echo-1'})
        self.assertEqual(turn['question'], parsed['sanitized_question'])
        self.assertEqual(turn['raw_question'], raw)
        stored = s.get('alice', session['id'])['turns'][0]
        self.assertEqual(stored['raw_question'], raw)
        self.assertNotIn(raw, stored['question'])
    def test_raw_question_validation_update_and_idempotency(self):
        from fastapi import HTTPException
        from backend.app import qa_sessions as s
        session=s.create('alice')
        first,fresh=s.accept('alice',session['id'],{'question':'国家助学金条件','raw_question':'国家助学金条件','client_request_id':'raw-1'})
        self.assertTrue(fresh)
        with self.assertRaises(HTTPException): s.accept('alice',session['id'],{'question':'国家助学金条件','raw_question':'x','client_request_id':'raw-bad'})
        with self.assertRaises(HTTPException): s.update_raw('alice',first['id'],'x')
        with self.assertRaises(HTTPException): s.update_raw('bob',first['id'],'新的原文')
        changed=s.update_raw('alice',first['id'],'新的原文')
        self.assertEqual(changed['raw_question'],'新的原文')
        self.assertEqual(s.update_raw('alice',first['id'],'新的原文')['raw_question'],'新的原文')
        with db.conn() as c:
            audits=c.execute("SELECT COUNT(*) FROM audit_log WHERE action='qa_turn_raw_updated' AND target=?",(first['id'],)).fetchone()[0]
        self.assertEqual(audits,1)
        old=s.create('alice')
        a,_=s.accept('alice',old['id'],{'question':'申请条件','client_request_id':'repair-1'})
        b,fresh2=s.accept('alice',old['id'],{'question':'申请条件','raw_question':'补充原文','client_request_id':'repair-1'})
        self.assertFalse(fresh2); self.assertEqual(a['id'],b['id']); self.assertEqual(b['raw_question'],'补充原文')

    def test_preparing_state_and_lease(self):
        from backend.app import qa_sessions as s
        from fastapi import HTTPException
        session=s.create('alice')
        turn,_=s.accept('alice',session['id'],{'question':'国家助学金申请条件','client_request_id':'life-1'})
        self.assertEqual(turn['state'],'accepted')
        with db.conn() as c:
            before=c.execute('SELECT lease_until FROM qa_turn WHERE id=?',(turn['id'],)).fetchone()[0]
        self.assertTrue(s.claim(turn['id']))
        self.assertFalse(s.claim(turn['id']))
        self.assertEqual(s.get_turn('alice',turn['id'])['state'],'preparing')
        with db.conn() as c:
            c.execute("UPDATE qa_turn SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",(turn['id'],))
        with db.conn() as c:
            self.assertNotEqual(c.execute('SELECT lease_until FROM qa_turn WHERE id=?',(turn['id'],)).fetchone()[0],before)
        self.assertTrue(s.renew(turn['id']))
        # A read must never mark a live turn as interrupted.
        with db.conn() as c:
            c.execute("UPDATE qa_turn SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",(turn['id'],))
        self.assertEqual(s.get_turn('alice',turn['id'])['state'],'preparing')
        self.assertTrue(s.finish(turn['id'],'completed',answer='已保存'))
        self.assertFalse(s.is_active(turn['id']))

    def test_handoff_single_consumption_and_progress_scope(self):
        from backend.app import qa_sessions as s
        from fastapi import HTTPException
        with db.conn() as c:
            c.execute("INSERT INTO triage_task(task_id,created_at,owner,policy_version,student_json,result_json,status,consent)"
                      " VALUES('task-a','now','alice','V1','{}','{}','triaged',1)")
        session=s.create('alice')
        turn,_=s.accept('alice',session['id'],{'question':'国家助学金申请条件','client_request_id':'ho-1'})
        s.finish(turn['id'],'completed',answer='已保存')
        handoff=s.create_handoff('alice',turn['id'])
        self.assertEqual(handoff['kind'],'triage')
        self.assertEqual(s.read_handoff('alice',handoff['id'])['consumed_task_id'],None)
        with self.assertRaises(HTTPException): s.read_handoff('bob',handoff['id'])
        s.consume_handoff('alice',handoff['id'],'task-a')
        self.assertEqual(s.read_handoff('alice',handoff['id'])['consumed_task_id'],'task-a')
        with self.assertRaises(HTTPException): s.consume_handoff('alice',handoff['id'],'task-b')
        progress=s.create_handoff('alice',turn['id'],kind='progress',task_id='task-a')
        self.assertEqual(s.read_handoff('alice',progress['id'])['task_id'],'task-a')
        with self.assertRaises(HTTPException): s.create_handoff('alice',turn['id'],kind='progress',task_id='task-b')


class FrontendEchoContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_raw_input_persistence_wiring(self):
        js = (self.ROOT / 'frontend/qa-workbench.js').read_text(encoding='utf-8')
        self.assertNotIn('rawEcho', js)
        self.assertIn('raw_question:question', js)
        self.assertIn('api/qa/turns/${tid}/raw-question', js)
        self.assertIn('data-edit-raw', js)
        self.assertIn('const history = await refreshHistory(); if (history.length) await openSession(history[0].id)', js)
        self.assertIn('await send(turn.raw_question || turn.question)', js)
        html = (self.ROOT / 'frontend/qa.html').read_text(encoding='utf-8')
        self.assertIn('?v=20261121f', html)


class RawQuestionMigrationTests(unittest.TestCase):
    def test_legacy_db_gets_raw_question_without_losing_rows(self):
        from backend.app import db, qa_sessions
        with tempfile.TemporaryDirectory() as d:
            legacy = Path(d)/'legacy.db'
            with closing(sqlite3.connect(legacy)) as c:
                c.execute("CREATE TABLE qa_turn (id TEXT PRIMARY KEY, session_id TEXT, client_request_id TEXT,"
                          " parent_turn_id TEXT, sanitized_question TEXT, context_json TEXT, evidence_json TEXT,"
                          " panel_json TEXT, validated_answer TEXT, citations_json TEXT, task_json TEXT, state TEXT,"
                          " mode TEXT, fallback_reason_code TEXT, prompt_version TEXT, created_at TEXT,"
                          " completed_at TEXT, lease_until TEXT)")
                c.execute("INSERT INTO qa_turn(id,sanitized_question,state) VALUES('t1','旧问题','completed')")
                c.commit()
            with patch.object(db,'APP_DB',legacy):
                qa_sessions.init_schema()
                with closing(db.conn()) as c:
                    cols={r['name'] for r in c.execute('PRAGMA table_info(qa_turn)')}
                    self.assertIn('raw_question',cols)
                    self.assertEqual(c.execute("SELECT raw_question FROM qa_turn WHERE id='t1'").fetchone()[0],'')
                    self.assertEqual(c.execute("SELECT sanitized_question FROM qa_turn WHERE id='t1'").fetchone()[0],'旧问题')


class LegacyResultCompatTests(unittest.TestCase):
    def test_result_json_without_items_is_tolerated(self):
        from backend.app import db
        self.assertEqual(db.task_items('{}'), [])
        self.assertEqual(db.task_items(''), [])
        self.assertEqual(db.task_items('{broken'), [])
        self.assertEqual(db.task_items('{"items":[{"project_id":"GZ"}]}')[0]['project_id'], 'GZ')


class PanelTests(EvidenceTests):
    def test_panel_is_frozen_and_intent_specific(self):
        from backend.app import qa_evidence as e, qa_panel
        with db.conn() as c: c.execute("INSERT INTO material_item(project_id,name,source_type) VALUES('GZ','合成材料甲','学校要求')")
        b=e.build('助学金材料',{'project_codes':['GZ'],'intent':'materials','policy_version':'V1'},self.policy)
        first=qa_panel.build(b)
        with db.conn() as c: c.execute("UPDATE material_item SET name='合成材料乙'")
        self.assertEqual(first,qa_panel.build(b))
        self.assertIn('合成材料甲',json.dumps(first,ensure_ascii=False))
        self.assertNotIn('合成材料乙',json.dumps(first,ensure_ascii=False))
        self.assertNotIn('steps',first)


class ProviderTests(EvidenceTests):
    def _facts(self, bundle):
        from backend.app import qa_evidence as e
        return e.to_factpack(bundle)['facts']

    def test_fact_consistency_rejects_fabrication(self):
        from backend.app import provider, qa_evidence as e
        bundle=e.build('助学金标准',{'project_codes':['GZ'],'intent':'benefit_standard','policy_version':'V1'},self.policy)
        facts=self._facts(bundle); fid=facts[0]['id']
        # 合法：只用给定事实组织语言；正文锚点与 claims 一致；原文描述性「符合条件」不误伤
        self.assertTrue(provider.validate_fact_consistency({'answer':f'国家助学金标准每年100元。[{fid}]','claims':[fid]},facts)[0])
        self.assertTrue(provider.validate_fact_consistency({'answer':f'符合条件的全日制在校生，标准为每年100元。[{fid}]','claims':[fid]},facts)[0])
        # 编造金额 / 文号 / 判定措辞 / 引用不存在事实 / 锚点缺失或不一致 / 结构非法
        for payload in ({'answer':f'国家助学金每年99999元。[{fid}]','claims':[fid]},
                        {'answer':f'依据〔2020〕99号。[{fid}]','claims':[fid]},
                        {'answer':f'你符合条件，可申请。[{fid}]','claims':[fid]},
                        {'answer':'标准100元。[V9:1]','claims':['V9:1']},
                        {'answer':'国家助学金标准每年100元。','claims':[fid]},
                        {'answer':f'国家助学金标准每年100元。[V9:1]','claims':['V9:1']},
                        {'answer':f'国家助学金标准每年100元。[{fid}]','claims':['c0']},
                        {'evidence_ids':['V1:1']}):
            self.assertFalse(provider.validate_fact_consistency(payload,facts)[0])

    def _online_env(self):
        return patch.dict(os.environ,{'FUND_LLM_MODE':'online','FUND_LLM_BASE':'http://invalid',
                                      'FUND_LLM_KEY':'test','FUND_LLM_MODEL':'test'})

    def test_online_validation_failure_is_failed_not_fake_success(self):
        from backend.app import provider, qa_evidence as e
        bundle=e.build('助学金标准',{'project_codes':['GZ'],'intent':'benefit_standard','policy_version':'V1'},self.policy)
        fid=self._facts(bundle)[0]['id']
        with self._online_env():
            with patch.object(provider,'_call_workbench',return_value={'answer':'国家助学金每年99999元。','claims':[fid]}) as model:
                result=provider.compose_workbench(bundle)
        self.assertEqual(model.call_count,1)  # 校验失败不重试
        self.assertEqual(result['state'],'failed'); self.assertEqual(result['mode'],'online')
        self.assertEqual(result['fallback_reason_code'],'validation_failed')
        self.assertNotIn('99999',result['text'])
        self.assertTrue(result['error_hint'])

    def test_online_failures_never_disguise_as_success(self):
        from backend.app import provider, qa_evidence as e
        bundle=e.build('助学金标准',{'project_codes':['GZ'],'intent':'benefit_standard','policy_version':'V1'},self.policy)
        for reason in ('auth_failed','rate_limited','network_error','provider_error','bad_response','bad_request'):
            with self._online_env():
                with patch.object(provider,'_call_workbench',side_effect=provider.ProviderFailure(reason)):
                    result=provider.compose_workbench(bundle)
            self.assertEqual(result['state'],'failed',reason)
            self.assertEqual(result['mode'],'online',reason)
            self.assertEqual(result['fallback_reason_code'],reason)
            self.assertTrue(result['error_hint'],reason)
            self.assertNotIn('标准每年',result['text'],reason)  # 不回退成看起来成功的规则答案

    def test_auth_and_rate_limit_no_retry(self):
        from backend.app import provider, qa_evidence as e
        bundle=e.build('助学金标准',{'project_codes':['GZ'],'intent':'benefit_standard','policy_version':'V1'},self.policy)
        for reason in ('auth_failed','rate_limited'):
            with self._online_env():
                with patch.object(provider,'_call_workbench',side_effect=provider.ProviderFailure(reason)) as model:
                    provider.compose_workbench(bundle)
            self.assertEqual(model.call_count,1,reason)

    def test_sanitize_error_hides_key(self):
        from backend.app import provider
        secret='sk-abcdefghijklmnopqrstuvwxyz'
        with patch.dict(os.environ,{'FUND_LLM_KEY':secret}):
            out=provider._sanitize_error(f'Bearer {secret} at https://api.example.com/v1 key={secret}')
        self.assertNotIn(secret,out)
        self.assertNotIn('https://',out)
        self.assertIn('redacted',out)

    def test_status_note_matches_no_disguise_failure_semantics(self):
        from backend.app import provider
        with patch.dict(os.environ, {'FUND_LLM_MODE': 'online', 'FUND_LLM_BASE': 'https://example.invalid',
                                     'FUND_LLM_KEY': 'k', 'FUND_LLM_MODEL': 'm'}):
            note = provider.status()['mode_note']
        self.assertNotIn('失败回退离线', note)
        self.assertIn('不伪装成功', note)
        with patch.dict(os.environ, {'FUND_LLM_MODE': 'mock'}):
            self.assertIn('联调', provider.status()['mode_note'])

    def test_modes_and_no_unconfirmed_personal_conclusion(self):
        from backend.app import provider, qa_evidence as e
        b=e.build('助学金',{'project_codes':['GZ'],'intent':'policy_scope','policy_version':'V1','personal_guidance':True},self.policy)
        for mode in ('offline','mock'):
            with patch.dict(os.environ,{'FUND_LLM_MODE':mode,'FUND_LLM_KEY':''}): result=provider.compose_workbench(b)
            self.assertEqual(result['mode'],'mock' if mode=='mock' else 'offline')
            self.assertNotIn('你可申请',result['text'])
        # online 缺少配置 -> 明确失败态，不伪装成功
        with patch.dict(os.environ,{'FUND_LLM_MODE':'online','FUND_LLM_KEY':''}):
            result=provider.compose_workbench(b)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['fallback_reason_code'],'missing_configuration')

class MetadataTests(EvidenceTests):
    def test_verification_backfill_and_explicit_scope_confirmation(self):
        from backend.app import qa_evidence as e
        from fastapi import HTTPException
        with db.conn() as c:
            c.execute("INSERT INTO policy_verify(doc_id,doc_no,issuer,effective_from,verifier,verified_at,note)"
                      " VALUES(1,'测试文号','测试发布机关','2026-01-01','admin_demo','2026-01-02T00:00:00+00:00','核文')")
        e.snapshot_metadata('V1',self.policy)
        with sqlite3.connect(self.policy) as c:
            meta=json.loads(c.execute('SELECT metadata_json FROM policy_snapshot_meta WHERE version_id=? AND doc_id=?',('V1',1)).fetchone()[0])
        self.assertTrue(meta['validity_confirmed'])
        self.assertFalse(meta['scope_confirmed'])
        self.assertEqual(e.resolve('V1',1,self.policy)['scope_status'],'needs_verification')
        e.confirm_meta('V1',1,{'scope_confirmed':True,'validity_confirmed':True,
                               'issuer':'测试发布机关','effective_from':'2026-01-01',
                               'effective_to':None,'verifier':'admin_demo'},'admin_demo')
        self.assertEqual(e.resolve('V1',1,self.policy)['scope_status'],'supported')
        with self.assertRaises(HTTPException):
            e.confirm_meta('V1',1,{'scope_confirmed':True,'validity_confirmed':True,
                                   'effective_from':'2026/01/01','verifier':'admin_demo'},'admin_demo')


class ExportReadabilityTests(unittest.TestCase):
    def test_anchors_become_readable_refs_and_no_machine_markers(self):
        from backend.app.qa_routes import _readable_answer, _export_markdown
        turn = {
            'question': '标准是多少',
            'state': 'completed', 'mode': 'online',
            'answer': '国家助学金每年3700元[pv-2026:351]，需核实[c0]。研究生每年6000元[pv-2026:352]。',
            'citations': [{'evidence_id': 'pv-2026:351', 'doc_no': '粤财规〔2026〕1号', 'article_no': '第十一条'},
                          {'evidence_id': 'pv-2026:352', 'doc_no': '粤财规〔2026〕2号', 'article_no': '第十二条'}],
            'context': {'policy_version': 'pv-2026'}, 'panel': {'evidence': []},
        }
        readable = _readable_answer(turn)
        self.assertNotIn('[pv-', readable)
        self.assertNotIn('[c0]', readable)
        self.assertIn('〔依据1〕', readable)
        self.assertIn('〔依据2〕', readable)
        md = _export_markdown({'topic': 't'}, [turn])
        self.assertNotIn('[pv-', md)
        self.assertNotIn('[c0]', md)
        self.assertIn('回答模式：在线证据编排', md)
        self.assertIn('处理状态：已完成', md)


if __name__=='__main__': unittest.main(verbosity=2)
