"""Real bounded Deep Agents + MySQL acceptance using explicit local credentials."""
from __future__ import annotations
import argparse,asyncio,json,os,sys,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


async def check(args):
    from dotenv import dotenv_values
    values=dotenv_values(args.env_file)
    for key in ['DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL']:
        if values.get(key): os.environ[key]=values[key]
    path=Path(args.db_credentials)
    if path.is_symlink() or path.stat().st_mode & 0o777 != 0o600: raise ValueError('Credentials must be regular 0600 file')
    credentials=json.loads(path.read_text())
    if credentials.get('MYSQL_DATABASE')!='pharma_stage1_test' or credentials.get('MYSQL_USER')!='tanyuan_stage1_reader' or credentials.get('MYSQL_HOST')!='127.0.0.1': raise ValueError('Only isolated test identity is allowed')
    os.environ.update(credentials)
    os.environ['TANYUAN_CALL_BUDGET_FILE']=str(Path(args.budget_file).resolve())
    from agent.main_agent import run_deep_agent
    from api.monitor import monitor
    from utils import path_utils
    trace=[];original=monitor.report
    async def record(thread,event,message,data=None):
        trace.append((event,data or {}))
        await original(thread,event,message,data)
    thread='stage5-'+uuid.uuid4().hex
    with patch.object(monitor,'report',side_effect=record):
        answer=await asyncio.wait_for(run_deep_agent('列出所有抗生素类药品，只需给出通用名，不生成文件。',thread),timeout=300)
    assert '阿莫西林' in answer and '头孢曲松' in answer and '未达标说明' not in answer,'Answer failed source/content review'
    assert any(event=='assistant_call' and data.get('assistant_name')=='数据库查询助手' for event,data in trace),'No real subagent delegation'
    assert any(data.get('tool_name')=='execute_sql_query' for event,data in trace),'No real SQL tool call'
    assert not list(path_utils.get_session_dir(thread).iterdir()),'Plain question produced files'
    target=path_utils.OUTPUT_DIR/('stage5-evidence-'+uuid.uuid4().hex)
    target.mkdir()
    (target/'result.json').write_text(json.dumps({'thread_id':thread,'answer':answer,'tool_names':[data.get('tool_name') for event,data in trace if data.get('tool_name')],'subagents':[data.get('assistant_name') for event,data in trace if data.get('assistant_name')]},ensure_ascii=False),encoding='utf-8')
    print('PASS: real Main Agent -> DB Sub-Agent -> SQL -> semantic review; plain answer has no files')
    print('Budget counters:',json.loads(Path(args.budget_file).read_text()))
    print('Artifacts:',target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',required=True)
    parser.add_argument('--db-credentials',required=True)
    parser.add_argument('--budget-file',required=True)
    args=parser.parse_args()
    try: asyncio.run(check(args))
    except Exception as exc:
        print('FAIL: stage 5 acceptance:',type(exc).__name__,'(sensitive details suppressed)')
        raise SystemExit(1)
