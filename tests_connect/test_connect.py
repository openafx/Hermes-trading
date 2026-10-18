import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from prop_connect.app import create_app
from prop_connect.settings import Settings
from prop_connect.jobs import Jobs
from prop_connect.connectors import Connectors
from hermes_lab.journal import Journal

ROOT=Path(__file__).resolve().parents[1]
TOKEN='test-key-not-a-real-credential-000000000'


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Settings(ROOT,Path(self.temp.name),TOKEN))
        self.context=TestClient(self.app,base_url='http://localhost:8765')
        self.client=self.context.__enter__()

    def tearDown(self):self.context.__exit__(None,None,None);self.temp.cleanup()

    def auth(self):return {'Authorization':'Bearer '+TOKEN}

    def test_health_exposes_no_credentials(self):
        r=self.client.get('/healthz');self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['execution_enabled']);self.assertNotIn(TOKEN,r.text)

    def test_api_requires_authentication(self):
        self.assertEqual(self.client.get('/api/state').status_code,401)

    def test_login_cookie_does_not_put_secret_in_page(self):
        r=self.client.post('/auth/login',json={'token':TOKEN})
        self.assertEqual(r.status_code,200);self.assertIn('HttpOnly',r.headers['set-cookie'])
        page=self.client.get('/');self.assertIn('Vue d’ensemble',page.text)
        self.assertNotIn(TOKEN,page.text)
        self.assertEqual(self.client.get('/api/state').status_code,200)

    def test_login_rejects_bad_key(self):
        self.assertEqual(self.client.post('/auth/login',json={'token':'bad'}).status_code,401)

    def test_foreign_origin_rejected(self):
        r=self.client.post('/auth/login',json={'token':TOKEN},headers={'Origin':'https://example-attacker.invalid'})
        self.assertEqual(r.status_code,403)

    def test_host_rebinding_rejected(self):
        self.assertEqual(self.client.get('/api/state',headers={**self.auth(),'Host':'attacker.invalid'}).status_code,403)

    def test_unknown_operations_are_not_queued(self):
        r=self.client.post('/api/jobs',json={'operation':'place_order','payload':{},'request_key':'one'},headers=self.auth())
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.app.state.jobs.list(),[])

    def test_secrets_rejected_from_job_payload(self):
        r=self.client.post('/api/jobs',json={'operation':'mcp_read','payload':{'arguments':{'api_key':'should-not-be-stored'}},'request_key':'one'},headers=self.auth())
        self.assertEqual(r.status_code,400);self.assertEqual(self.app.state.jobs.list(),[])

    def test_report_job_executes(self):
        r=self.client.post('/api/jobs',json={'operation':'journal_report','payload':{'profile':{'size':50000}},'request_key':'report-1'},headers=self.auth())
        self.assertEqual(r.status_code,200);jid=r.json()['id']
        for _ in range(50):
            job=self.client.get('/api/jobs/'+jid,headers=self.auth()).json()
            if job['status'] in {'succeeded','failed'}:break
            time.sleep(.02)
        self.assertEqual(job['status'],'succeeded');self.assertFalse(job['result']['execution_authorized'])

    def test_all_repos_in_registry(self):
        r=self.client.get('/api/integrations',headers=self.auth())
        self.assertEqual(len(r.json()['repositories']),11)
        q=next(x for x in r.json()['repositories'] if x['id']=='dearvn')
        self.assertEqual(q['runtime'],'quarantined')
        self.assertFalse(q['remote_connected'])

    def test_example_stays_outside_persistent_journal(self):
        r=self.client.post('/api/analyze',json={'example':True,'profile':{'size':50000}},headers=self.auth())
        self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()['events']),12)
        self.assertEqual(self.app.state.journal.events(),[])

    def test_mcp_endpoint_is_authenticated(self):
        r=self.client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},headers={'Accept':'application/json, text/event-stream'})
        self.assertEqual(r.status_code,401)

    def test_mcp_initialize_and_tools_list(self):
        h={**self.auth(),'Accept':'application/json, text/event-stream'}
        r=self.client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},headers=h)
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},headers={**h,'MCP-Protocol-Version':'2025-03-26'})
        self.assertEqual(r.status_code,200,r.text)
        names={x['name'] for x in r.json()['result']['tools']}
        self.assertIn('trading_report',names);self.assertNotIn('place_order',names)

    def test_research_gate_is_closed_without_operator_enablement(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):self.app.state.connectors.execute('hermes_review',{})

    def test_arbitrary_document_path_rejected(self):
        with self.assertRaises(ValueError):self.app.state.connectors.read_document('investorskills','../../secret')

    def test_tradingagents_probe_is_free_but_research_stays_gated(self):
        from types import SimpleNamespace
        with patch.dict(os.environ,{'PROP_TRADINGAGENTS_PYTHON':sys.executable,'OPENAI_API_KEY':'not-for-probes'},clear=True):
            with patch('prop_connect.connectors.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{"ok":true,"result":{}}')) as run:
                self.app.state.connectors.worker('tradingagents','probe',{})
                self.assertNotIn('OPENAI_API_KEY',run.call_args.kwargs['env'])
                with self.assertRaises(ValueError):self.app.state.connectors.worker('tradingagents','research',{})
                self.assertEqual(run.call_count,1)


class JobTests(unittest.TestCase):
    def test_job_idempotency_and_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            calls=[]
            def execute(op,p):calls.append(op);return {'done':True}
            j=Jobs(Path(td)/'jobs.db',execute)
            a=j.submit('probe',{},'one');b=j.submit('probe',{},'one')
            self.assertEqual(a['id'],b['id'])
            with self.assertRaises(ValueError):j.submit('other',{},'one')
            j.close();self.assertEqual(calls,['probe'])

    def test_error_does_not_persist_upstream_secret(self):
        with tempfile.TemporaryDirectory() as td:
            def execute(*args):raise RuntimeError('Bearer super-secret-do-not-log')
            j=Jobs(Path(td)/'jobs.db',execute);a=j.submit('probe',{},'one');j.close()
            text=json.dumps(j.get(a['id']));self.assertNotIn('super-secret',text)
            self.assertIn('failed',text)


class MigrationTests(unittest.TestCase):
    def test_additive_installer_does_not_touch_history(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from install_into_existing import install
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'Hermes-trading';(root/'state').mkdir(parents=True)
            history=root/'state/trades.jsonl';history.write_text('existing-history\n')
            start=root/'Dockerfile';start.write_text('existing-entrypoint')
            plan=install(ROOT,root,False)
            self.assertFalse((root/'extensions').exists())
            install(ROOT,root,True)
            self.assertEqual(history.read_text(),'existing-history\n');self.assertEqual(start.read_text(),'existing-entrypoint')
            with self.assertRaises(ValueError):install(ROOT,root,True)

    def test_hermes_config_is_only_a_proposal(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from configure_hermes import proposal
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);original=root/'config.yaml';out=root/'proposed.yaml'
            original.write_text('model: keep-me\nmcp_servers:\n  old:\n    url: https://example.org/mcp\n')
            before=original.read_bytes();proposal(original,out,'http://127.0.0.1:8765/mcp')
            self.assertEqual(original.read_bytes(),before)
            self.assertIn('prop_connect',out.read_text());self.assertIn('old:',out.read_text())
            with self.assertRaises(ValueError):proposal(original,out,'http://127.0.0.1:8765/mcp')


if __name__=='__main__':unittest.main()
