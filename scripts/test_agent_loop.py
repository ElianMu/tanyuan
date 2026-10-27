"""Stage 5 offline tests; graph tests use a fake model, not a real LLM."""
from __future__ import annotations
import asyncio,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from agent import main_agent as m
from tools.reflect_check import hard_check
from utils import path_utils
from utils.call_budget import reserve
from api import context
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs): return self


def result(text='有效答案',**kwargs):
    return {'report':text,'messages':[AIMessage(content=text)],'report_requested':False,'original_query':'问题','evidence':'证据',**kwargs}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        patcher=patch.object(path_utils,'OUTPUT_DIR',Path(self.tmp.name)/'output');patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(path_utils,'UPLOAD_DIR',Path(self.tmp.name)/'upload');patcher.start();self.addCleanup(patcher.stop)
        self.addCleanup(context.reset_session)

    def test_intent_preserves_plain_answers_and_explicit_formats(self):
        for query in ['列出所有抗生素类药品','报告是什么？','仅回答，不要生成文件','生成报告但不要生成文件']:
            self.assertFalse(m.task_policy(query).report_requested)
        self.assertTrue(m.task_policy('生成销售报告').require_pdf)
        self.assertFalse(m.task_policy('生成 Markdown 销售报告，不要 PDF').require_pdf)
        self.assertTrue(m.task_policy('生成 PDF 销售报告').require_pdf)

    def test_hard_checks_report_vs_answer_and_sources(self):
        self.assertEqual(hard_check(result()),[])
        self.assertIn('报告篇幅不足 1000 字',hard_check(result(report_requested=True)))
        self.assertIn('回答为空',hard_check(result('')))
        issues=hard_check(result('TODO',require_pdf=True,pdf_produced=False,required_sources=['knowledge','web_search'],called_sources=['knowledge','web_search']))
        for fragment in ['占位符','PDF','两个不同角度','知识库检索未完成三次']:
            self.assertTrue(any(fragment in item for item in issues))

    def test_soft_failure_is_returned_after_three_rounds(self):
        async def run():
            with patch.object(m,'create_deep_agent',return_value=object()),patch.object(m,'main_agent_run',new_callable=AsyncMock,return_value=result()) as run,patch.object(m,'soft_review',new_callable=AsyncMock,return_value='不通过：证据不足') as review:
                output=await m.run_deep_agent('问题','soft')
                self.assertEqual(run.await_count,3);self.assertEqual(review.await_count,3)
                self.assertIn('证据不足',output);self.assertIsNone(context.get_thread_id())
        asyncio.run(run())

    def test_hard_failure_skips_soft_review_and_retries_history(self):
        async def run():
            with patch.object(m,'create_deep_agent',return_value=object()),patch.object(m,'main_agent_run',new_callable=AsyncMock,side_effect=[result(''),result('最终答案')]) as run,patch.object(m,'soft_review',new_callable=AsyncMock,return_value=None) as review:
                self.assertEqual(await m.run_deep_agent('原始问题','hard'),'最终答案')
                self.assertEqual(run.await_count,2);self.assertEqual(review.await_count,1)
                self.assertIn('原始问题',run.await_args_list[1].kwargs['messages'][-1].content)
        asyncio.run(run())

    def test_context_restored_on_failure_and_invalid_id_before_graph(self):
        outer=path_utils.get_session_dir('outer');context.set_session('outer',str(outer))
        async def run():
            with patch.object(m,'create_deep_agent',side_effect=RuntimeError('failure')):
                with self.assertRaises(RuntimeError): await m.run_deep_agent('问题','inner')
            self.assertEqual(context.get_thread_id(),'outer')
            with patch.object(m,'create_deep_agent') as create:
                with self.assertRaises(path_utils.InvalidSessionIDError): await m.run_deep_agent('问题','../outside')
                create.assert_not_called()
        asyncio.run(run())

    def test_runtime_tool_policy_rejects_unsafe_capabilities(self):
        p=m._policy.set(m.task_policy('普通问题'));e=m._evidence.set(m.Evidence())
        self.addCleanup(m._policy.reset,p);self.addCleanup(m._evidence.reset,e)
        for name,args in [('execute',{}),('write_file',{}),('generate_markdown',{}),('task',{'subagent_type':'general-purpose'})]:
            with self.assertRaises(ValueError): m.ToolPolicy().check(SimpleNamespace(tool_call={'name':name,'args':args}))

    def test_actual_deepagents_graph_blocks_plain_answer_file_tool(self):
        fake=ToolModel(responses=[AIMessage(content='',tool_calls=[{'name':'generate_markdown','args':{'content':'forbidden','filename':'bad.md'},'id':'t1','type':'tool_call'}]),AIMessage(content='直接回答')])
        async def run():
            with patch.object(m,'get_llm',return_value=fake),patch.object(m,'soft_review',new_callable=AsyncMock,return_value=None):
                self.assertEqual(await m.run_deep_agent('普通问题','graph'),'直接回答')
            self.assertEqual(list(path_utils.get_session_dir('graph').iterdir()),[])
        asyncio.run(run())

    def test_real_tool_evidence_does_not_count_failed_requests(self):
        async def run():
            evidence=m.Evidence()
            await evidence.on_tool_start({'name':'internet_search'},'',run_id='1',inputs={'query':'angle'})
            await evidence.on_tool_end('网络检索失败：timeout',run_id='1')
            self.assertNotIn('web_search',evidence.called)
            await evidence.on_tool_start({'name':'internet_search'},'',run_id='2',inputs={'query':'angle'})
            await evidence.on_tool_end('URL: https://example.com',run_id='2')
            self.assertEqual(evidence.counts['web_search'],1)
        asyncio.run(run())

    def test_persistent_call_budget_refuses_overrun(self):
        path=Path(self.tmp.name)/'budget.json';path.write_text(json.dumps({'deepseek':{'used':0,'limit':1}}));path.chmod(0o600)
        with patch.dict(os.environ,{'TANYUAN_CALL_BUDGET_FILE':str(path)}):
            reserve('deepseek')
            with self.assertRaises(RuntimeError): reserve('deepseek')
        self.assertEqual(json.loads(path.read_text())['deepseek']['used'],1)


if __name__=='__main__': unittest.main(verbosity=2)
