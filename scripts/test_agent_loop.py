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

    def test_model_schema_hides_disallowed_builtin_tools(self):
        class Request:
            tools=[{'name':'execute'},{'name':'write_file'},{'name':'task'},{'name':'read_file_content'}]
            def override(self,**kwargs): return kwargs
        filtered=m.ToolPolicy().wrap_model_call(Request(),lambda request:request)
        self.assertEqual([t['name'] for t in filtered['tools']],['task','read_file_content'])

    def test_review_balances_sources_and_keeps_database_provenance(self):
        evidence=m.Evidence()
        evidence.records=[{'source':'db_query','tool':'get_table_data','arguments':{'table_name':'sales_records'},'content':'x'*7000+'region,unit_price\n华东区,15.00'}]
        evidence.records.extend({'source':'web_search','tool':'internet_search','arguments':{},'content':'x'*20000} for _ in range(3))
        payload=evidence.review_evidence()
        self.assertIn('华东区',payload['db_query']['records'])
        self.assertIn('unit_price',payload['db_query']['records'])
        self.assertFalse(payload['db_query']['truncated'])
        self.assertTrue(payload['web_search']['truncated'])

    def test_failed_tool_calls_reach_review_without_counting_as_sources(self):
        async def run():
            evidence=m.Evidence()
            await evidence.on_tool_start({'name':'execute_sql_query'},'',run_id='a',inputs={'sql':'SELECT 1'})
            await evidence.on_tool_end('连接数据库失败：数据库操作失败，请检查服务、只读权限和超时配置',run_id='a')
            payload=evidence.review_evidence()
            self.assertIn('连接数据库失败',payload['failed_calls']['records'])
            self.assertNotIn('db_query',evidence.called)
            self.assertEqual(evidence.records,[])
        asyncio.run(run())

    def test_semantic_review_receives_actual_runtime_proof(self):
        async def run():
            fake=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='{"passed":true,"issues":[]}')))
            fake.bind=lambda **kwargs:fake
            data=result()
            data['runtime_proof']={'called_sources':['db_query'],'files':[{'name':'report.pdf','bytes':12000}]}
            with patch.object(m,'get_llm',return_value=fake):
                self.assertIsNone(await m.soft_review(data))
            messages=fake.ainvoke.await_args.args[0]
            payload=json.loads(messages[1].content)
            self.assertEqual(payload['runtime_proof']['files'][0]['name'],'report.pdf')
            self.assertEqual(payload['runtime_proof']['called_sources'],['db_query'])
            self.assertIn('程序核实',messages[0].content)
        asyncio.run(run())

    def test_semantic_json_validation_fails_closed(self):
        self.assertIsNone(m.parse_review('{"passed":true,"issues":[]}'))
        self.assertIn('数据不一致',m.parse_review('{"passed":false,"issues":["数据不一致"]}'))
        for text in ['通过','', '{"passed":true,"issues":["尚有错误"]}', '{"passed":false,"issues":[]}',
                     '{"passed":"true","issues":[]}', '{"passed":true,"issues":[],"unknown":1}']:
            self.assertIn('合法且一致',m.parse_review(text))

    def test_child_partial_summary_cannot_hide_successful_tool_evidence(self):
        evidence=m.Evidence();evidence.records=[{'source':'db_query','tool':'execute_sql_query','arguments':{},'content':'total\n2153200.00'}]
        token=m._evidence.set(evidence);self.addCleanup(m._evidence.reset,token)
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command
        request=SimpleNamespace(tool_call={'name':'task','args':{'subagent_type':'数据库查询助手'}})
        original=Command(update={'messages':[ToolMessage(content='model call limit reached',tool_call_id='child')]})
        output=m.ToolPolicy()._verified_result(request,original)
        self.assertIn('2153200.00',output.update['messages'][0].content)
        self.assertNotIn('2153200.00',original.update['messages'][0].content)

    def test_actual_deepagents_graph_blocks_plain_answer_file_tool(self):
        fake=ToolModel(responses=[AIMessage(content='',tool_calls=[{'name':'generate_markdown','args':{'content':'forbidden','filename':'bad.md'},'id':'t1','type':'tool_call'}]),AIMessage(content='直接回答')])
        async def run():
            with patch.object(m,'get_llm',return_value=fake),patch.object(m,'soft_review',new_callable=AsyncMock,return_value=None):
                self.assertEqual(await m.run_deep_agent('普通问题','graph'),'直接回答')
            self.assertEqual(list(path_utils.get_session_dir('graph').iterdir()),[])
        asyncio.run(run())

    def test_actual_graph_report_tools_and_artifact_evidence(self):
        text='# 测试报告\n\n'+('这是公开测试内容，用于验证中文报告与文件生成。'*70)
        def call(name,args,key):
            return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':key,'type':'tool_call'}])
        fake=ToolModel(responses=[call('read_file_content',{'filename':'memo.txt'},'r'),
            call('generate_markdown',{'content':text,'filename':'report.md'},'m'),
            call('convert_md_to_pdf',{'md_filename':'report.md'},'p'),AIMessage(content='已成功创建')])
        upload=path_utils.get_upload_dir('reportgraph');upload.mkdir(parents=True)
        (upload/'memo.txt').write_text('公开测试内容')
        async def run():
            with patch.object(m,'get_llm',return_value=fake),patch.object(m,'soft_review',new_callable=AsyncMock,return_value=None):
                answer=await m.run_deep_agent('生成 PDF 报告','reportgraph')
            self.assertEqual(answer,text)
            self.assertTrue((path_utils.get_session_dir('reportgraph')/'report.pdf').read_bytes().startswith(b'%PDF-'))
        asyncio.run(run())

    def test_actual_subagent_graph_retains_task_context(self):
        def list_sql_tables() -> str:
            """Offline test table listing."""
            return '可用的表：drugs,inventory,sales_records'
        fake=ToolModel(responses=[
            AIMessage(content='',tool_calls=[{'name':'task','args':{'subagent_type':'数据库查询助手','description':'列出表'},'id':'delegate','type':'tool_call'}]),
            AIMessage(content='',tool_calls=[{'name':'list_sql_tables','args':{},'id':'list','type':'tool_call'}]),
            AIMessage(content='已查到三张业务表'),AIMessage(content='已查到三张业务表')])
        child={**m.SUBAGENTS[1],'tools':[list_sql_tables]}
        async def run():
            with patch.object(m,'get_llm',return_value=fake),patch.object(m,'SUBAGENTS',[child]),patch.object(m,'soft_review',new_callable=AsyncMock,return_value=None):
                answer=await m.run_deep_agent('查询数据库业务表','childgraph')
            self.assertNotIn('未达标说明',answer)
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
