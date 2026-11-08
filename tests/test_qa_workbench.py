"""Synthetic fixtures only; never opens live policy/student databases."""
import json, os, sqlite3, tempfile, unittest
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
        t,_=s.accept('alice',session['id'],{'question':'我叫张三，手机号13812345678，父亲收入12345元，国家助学金我能申请吗','client_request_id':'r1'})
        raw=json.dumps(t,ensure_ascii=False)
        for word in ('张三','13812345678','12345','父亲'): self.assertNotIn(word,raw)
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
    def test_rejects_unproven_claim_even_with_real_citation(self):
        from backend.app import provider, qa_evidence as e
        bundle=e.build('助学金标准',{'project_codes':['GZ'],'intent':'benefit_standard','policy_version':'V1'},self.policy)
        for payload in ({'evidence_ids':['V1:1'],'answer':'测试文号规定你一定领取99999元'},
                        {'evidence_ids':['V9:1']}, {'evidence_ids':['V1:1'],'conclusion':'eligible'}):
            self.assertFalse(provider.validate_workbench(payload,bundle)[0])
        self.assertTrue(provider.validate_workbench({'evidence_ids':['V1:1']},bundle)[0])
        with patch.dict(os.environ,{'FUND_LLM_MODE':'online','FUND_LLM_BASE':'http://invalid','FUND_LLM_KEY':'test','FUND_LLM_MODEL':'test'}):
            with patch.object(provider,'_call_workbench',return_value={'evidence_ids':['V1:1'],'answer':'99999元'}) as model:
                result=provider.compose_workbench(bundle)
        self.assertEqual(model.call_count,2);self.assertEqual(result['mode'],'offline')
        self.assertEqual(result['fallback_reason_code'],'validation_failed'); self.assertNotIn('99999',result['text'])
    def test_modes_and_no_unconfirmed_personal_conclusion(self):
        from backend.app import provider, qa_evidence as e
        b=e.build('助学金',{'project_codes':['GZ'],'intent':'policy_scope','policy_version':'V1','personal_guidance':True},self.policy)
        for mode in ('offline','mock','online'):
            with patch.dict(os.environ,{'FUND_LLM_MODE':mode,'FUND_LLM_KEY':''}): result=provider.compose_workbench(b)
            self.assertEqual(result['mode'],'mock' if mode=='mock' else 'offline')
            self.assertNotIn('你可申请',result['text'])

if __name__=='__main__': unittest.main(verbosity=2)
