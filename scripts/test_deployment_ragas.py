"""Offline deployment/RAGAS regression; no external requests or private data."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from api import server
import eval_ragas


class DeploymentTests(unittest.TestCase):
    def test_built_page_assets_health_and_api_precedence(self):
        self.assertTrue((server.FRONTEND_DIST/'index.html').is_file(), '先构建 frontend/dist')
        with TestClient(server.app) as client:
            response=client.get('/')
            self.assertEqual(response.status_code,200)
            self.assertIn('text/html',response.headers['content-type'])
            self.assertIn('no-cache',response.headers['cache-control'])
            import re
            for url in re.findall(r'(?:src|href)="(/assets/[^"]+)"',response.text):
                asset=client.get(url)
                self.assertEqual(asset.status_code,200)
                self.assertNotIn('text/html',asset.headers['content-type'])
            self.assertEqual(client.get('/api/health').json()['status'],'ok')
            self.assertEqual(client.get('/api/files').status_code,422)
            for url in ['/api/missing','/ws/missing','/assets/missing.js','/.env','/pyproject.toml','/assets/%2e%2e/%2e%2e/pyproject.toml']:
                self.assertEqual(client.get(url).status_code,404,url)
            with client.websocket_connect('/ws/deployment_check') as ws:
                ws.send_text('ping')
                self.assertEqual(ws.receive_json()['type'],'pong')
            with self.assertRaises(WebSocketDisconnect) as closed:
                with client.websocket_connect('/ws/unknown/extra'): pass
            self.assertEqual(closed.exception.code,1008)
        self.assertNotIn('agent.main_agent',sys.modules)

    def test_backend_without_frontend(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'FRONTEND_DIST',Path(tmp)):
            with TestClient(server.app) as client:
                self.assertEqual(client.get('/').json()['status'],'ok')
                self.assertEqual(client.get('/api/health').status_code,200)


class RagasTests(unittest.TestCase):
    def test_judging_uses_fresh_client_and_resume_skips_generation(self):
        import os
        rows=[dict(user_input='q',response='a',reference='r',retrieved_contexts=['public'])]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);budget=folder/'budget.json';saved=folder/'rows.json'
            budget.write_text('{"deepseek":{"used":0,"limit":10}}');budget.chmod(0o600)
            saved.write_text(json.dumps(rows))
            generator,judge=object(),object()
            args=['eval_ragas.py','--allow-context-upload','--budget-file',str(budget),'--rows',str(saved),'--output-dir',str(folder)]
            with patch.object(sys,'argv',args), patch.dict(os.environ,{},clear=False), \
                 patch('agent.llm.get_llm',side_effect=[generator,judge]), \
                 patch('utils.knowledge_utils.get_embedding',return_value='local'), \
                 patch.object(eval_ragas,'build_rows',new_callable=AsyncMock) as generate, \
                 patch.object(eval_ragas,'evaluate_rows',return_value={'means':{},'scores':[]}) as evaluate:
                eval_ragas.main()
                generate.assert_not_called()
                evaluate.assert_called_once_with(rows,judge,'local')

    def test_actual_ragas_engine_and_per_call_budget(self):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.embeddings import Embeddings
        from langchain_core.messages import AIMessage
        from utils.call_budget import BudgetCallback
        import os
        class LocalEmbeddings(Embeddings):
            def embed_documents(self,texts): return [[1.,0.] for _ in texts]
            def embed_query(self,text): return [1.,0.]
        outputs=[
            {'statements':['The sky is blue.']},
            {'statements':[{'statement':'The sky is blue.','reason':'provided','verdict':1}]},
            {'question':'What colour is the sky?','noncommittal':0},
            {'reason':'provided','verdict':1},
            {'classifications':[{'statement':'The sky is blue.','reason':'provided','attributed':1}]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            budget=Path(tmp)/'budget.json'
            budget.write_text(json.dumps({'deepseek':{'used':0,'limit':5}}));budget.chmod(0o600)
            with patch.dict(os.environ,{'TANYUAN_CALL_BUDGET_FILE':str(budget)}):
                llm=GenericFakeChatModel(messages=iter([AIMessage(content=json.dumps(x)) for x in outputs]),callbacks=[BudgetCallback()])
                result=eval_ragas.evaluate_rows([dict(user_input='What colour is the sky?',response='The sky is blue.',reference='The sky is blue.',retrieved_contexts=['The sky is blue.'])],llm,LocalEmbeddings())
            self.assertEqual(json.loads(budget.read_text())['deepseek']['used'],5)
            self.assertEqual(result['means']['faithfulness'],1.)

    def test_contexts_keep_each_chunk(self):
        self.assertEqual(len(eval_ragas.split_contexts('[来源: a]\nfirst\n\n[来源: b]\nsecond')),2)
        for failure in ['知识库检索失败：超时','知识库中未检索到相关内容','[来源: a]\n']:
            with self.assertRaises(ValueError): eval_ragas.split_contexts(failure)

    def test_retrieval_failure_does_not_call_model(self):
        llm=SimpleNamespace(ainvoke=AsyncMock())
        retrieve=SimpleNamespace(ainvoke=AsyncMock(return_value='知识库检索失败：超时'))
        with self.assertRaises(ValueError):
            asyncio.run(eval_ragas.build_rows(llm,[{'question':'q','ground_truth':'ref'}],retrieve))
        llm.ainvoke.assert_not_called()

    def test_build_rows(self):
        llm=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='answer')))
        retrieve=SimpleNamespace(ainvoke=AsyncMock(return_value='[来源: public]\ncontext'))
        rows=asyncio.run(eval_ragas.build_rows(llm,[{'question':'q','ground_truth':'ref'}],retrieve))
        self.assertEqual(rows,[dict(user_input='q',response='answer',retrieved_contexts=['[来源: public]\ncontext'],reference='ref')])

    def test_questions_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'questions.json'
            for value in [[],[{}],[{'question':'q','ground_truth':''}]]:
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):eval_ragas.load_questions(path)

    def test_no_implicit_remote_run(self):
        with patch.object(sys,'argv',['eval_ragas.py']), self.assertRaises(SystemExit) as caught:
            eval_ragas.main()
        self.assertEqual(caught.exception.code,2)


if __name__=='__main__': unittest.main(verbosity=2)
