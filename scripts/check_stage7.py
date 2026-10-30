"""Real localhost HTTP/WS E2E acceptance. Explicit credentials and persistent quota."""
from __future__ import annotations
import argparse,asyncio,json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
import httpx
from websockets.asyncio.client import connect
ROOT=Path(__file__).resolve().parent.parent


async def task(client,query,case,artifacts):
    response=await client.post('/api/session');response.raise_for_status();thread=response.json()['thread_id']
    (artifacts/(case+'-session.json')).write_text(json.dumps({'thread_id':thread,'query':query},ensure_ascii=False),encoding='utf-8')
    upload=await client.post('/api/upload',data={'thread_id':thread},files={'files':('memo.txt','所有数据仅用于系统验收，不作为医疗建议。'.encode())});upload.raise_for_status()
    events=[]
    async with connect('ws://127.0.0.1:8000/ws/'+thread+'?after=0',max_size=2*1024*1024) as ws, _journal(artifacts/(case+'-events.jsonl')) as journal:
        started=await client.post('/api/task',json={'query':query,'thread_id':thread});started.raise_for_status()
        while True:
            event=json.loads(await asyncio.wait_for(ws.recv(),timeout=310))
            if event.get('type')!='monitor_event': continue
            assert event['thread_id']==thread,'Cross-session event'
            events.append(event)
            journal.write(json.dumps(event,ensure_ascii=False)+'\n');journal.flush()
            if event['event'] in ('assistant_call','tool_end','reflection','task_result','error'):
                print('EVENT:',event['event'],event.get('data',{}).get('tool_name',''),flush=True)
            if event['event'] in ('task_result','error','cancelled'): break
    (artifacts/(case+'-events.json')).write_text(json.dumps(events,ensure_ascii=False),encoding='utf-8')
    status=(await client.get('/api/task/'+thread)).json()
    (artifacts/(case+'-status.json')).write_text(json.dumps(status,ensure_ascii=False),encoding='utf-8')
    assert status['status']=='completed','Agent failed quality checks or execution; evidence retained'
    files=(await client.get('/api/files',params={'thread_id':thread})).json()['files']
    if case=='report':
        names={e.get('data',{}).get('assistant_name') for e in events if e['event']=='assistant_call'}
        assert {'数据库查询助手','网络检索助手','内部知识库助手'} <= names,'Missing real subagent calls'
        assert any(e['event']=='reflection' and e['data'].get('passed') for e in events),'No successful reflection'
        md=[f for f in files if f['name'].endswith('.md')];pdf=[f for f in files if f['name'].endswith('.pdf')]
        assert md and pdf,'Missing Markdown/PDF artifact'
        markdown=await client.get('/api/download',params={'thread_id':thread,'path':md[-1]['path']})
        assert len(markdown.text.strip())>=1000,'Report under 1000 characters'
        download=await client.get('/api/download',params={'thread_id':thread,'path':pdf[-1]['path']})
        assert download.content.startswith(b'%PDF-'),'Invalid PDF download'
        (artifacts/'downloaded-report.pdf').write_bytes(download.content)
        other=(await client.post('/api/session')).json()['thread_id']
        forbidden=await client.get('/api/download',params={'thread_id':other,'path':pdf[-1]['path']})
        assert forbidden.status_code==403,'Cross-session file access allowed'
    else:
        assert '阿莫西林' in status['result'] and '头孢曲松' in status['result'],'Incorrect SQL answer'
        assert not [f for f in files if f['name']!='memo.txt'],'Plain question generated files'
    print('PASS: real HTTP + WS + Agent case:',case,'events:',len(events),'session:',thread,flush=True)


from contextlib import asynccontextmanager

@asynccontextmanager
async def _journal(path):
    with path.open('x',encoding='utf-8') as stream:
        yield stream


async def run(args):
    from dotenv import dotenv_values
    values=dotenv_values(args.env_file)
    env=os.environ.copy()
    for name in ['DEEPSEEK_API_KEY','TAVILY_API_KEY','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL']:
        if values.get(name):env[name]=values[name]
    credential_path=Path(args.db_credentials)
    assert not credential_path.is_symlink() and credential_path.stat().st_mode & 0o777==0o600
    credentials=json.loads(credential_path.read_text())
    assert credentials.get('MYSQL_USER')=='tanyuan_stage1_reader' and credentials.get('MYSQL_DATABASE')=='pharma_stage1_test' and credentials.get('MYSQL_HOST')=='127.0.0.1'
    env.update(credentials)
    index=Path(args.public_index).resolve();manifest=json.loads((index/'tanyuan-index.json').read_text())
    assert {source['source'] for source in manifest['sources']}=={'antibiotics.md','inventory.md','sales.md'} and manifest['chunks']==3,'Only reviewed public acceptance fixture index allowed'
    env.update(VECTOR_STORE_DIR=str(index),EMBEDDING_MODEL_PATH=manifest['model'],TANYUAN_CALL_BUDGET_FILE=str(Path(args.budget_file).resolve()),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        probe.bind(('127.0.0.1',8000))
    artifacts=ROOT/'output'/('stage7-e2e-'+uuid.uuid4().hex);artifacts.mkdir(parents=True)
    env['TANYUAN_ACCEPTANCE_EVIDENCE_DIR']=str(artifacts)
    with (artifacts/'server.log').open('w') as log:
        child=subprocess.Popen([sys.executable,'-B','main.py'],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=15,trust_env=False) as client:
                for _ in range(100):
                    if child.poll() is not None:raise RuntimeError('Server failed to start')
                    try:
                        response=await client.get('/')
                        if response.status_code==200:break
                    except httpx.ConnectError:pass
                    await asyncio.sleep(.1)
                else:raise RuntimeError('Server startup timeout')
                if args.case=='report':
                    query='读取已上传的 memo.txt。根据药品销售和库存数据库、互联网公开资料和内部知识库管理规范，生成一份不少于1000字的中文 PDF 销售库存分析报告。必须实际委派三个专业助手：数据库查询测试数据；网络检索两个不同角度的公开库存管理资料；知识库检索三次并引用实际来源。报告明确所有业务数据是测试种子数据，不给医疗建议。先生成 Markdown 再转 PDF。只使用公开测试语料，不搜索私有内容。'
                else:query='列出所有抗生素类药品，只需给出通用名，不生成文件。'
                await task(client,query,args.case,artifacts)
        finally:
            child.terminate()
            try:await asyncio.to_thread(child.wait,10)
            except subprocess.TimeoutExpired:child.kill();await asyncio.to_thread(child.wait)
            print('Artifacts:',artifacts,flush=True)
            print('Budget counters:',json.loads(Path(args.budget_file).read_text()),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['env-file','db-credentials','budget-file','public-index']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--case',choices=['report','answer'],required=True)
    try:asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        print('FAIL: E2E:',type(exc).__name__,'(sensitive exception details suppressed)')
        raise SystemExit(1)
