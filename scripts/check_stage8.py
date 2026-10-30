"""Stage 8 offline security acceptance: path bounds, SQL write rejection, secrets, debug output."""
from __future__ import annotations
import ast,re,subprocess,sys,tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from api import server
from tools import mysql_tools as db
from utils import path_utils

RUNTIME=['agent','api','tools','utils','main.py']
SECRET=re.compile(r'sk-[A-Za-z0-9]{16,}|tvly-[A-Za-z0-9]{8,}|(?i:(api_key|password|secret)\s*[:=]\s*["\'][^"\'\s]{8,}["\'])')
results=[]


def check(name,ok,detail=''):
    results.append(ok);print(('PASS' if ok else 'FAIL')+': '+name+(f' ({detail})' if detail else ''))


def paths():
    with tempfile.TemporaryDirectory() as temp,patch.object(path_utils,'OUTPUT_DIR',Path(temp)):
        client=TestClient(server.app);server._sessions.clear()
        first,second=(client.post('/api/session').json()['thread_id'] for _ in range(2))
        own=path_utils.get_session_dir(first)/'own.txt';own.write_text('own')
        other=path_utils.get_session_dir(second)/'other.txt';other.write_text('private')
        (own.parent/'link.txt').symlink_to(other)
        check('own session download allowed',client.get('/api/download',params={'thread_id':first,'path':'own.txt'}).text=='own')
        probes=['../'+second+'/other.txt',str(other),'../../../.env','/etc/passwd','link.txt','..']
        for route in ['/api/files','/api/download']:
            for probe in probes:
                response=client.get(route,params={'thread_id':first,'path':probe})
                check(f'{route} rejects {probe!r}',response.status_code in (403,404) and 'private' not in response.text,str(response.status_code))
        response=client.get('/api/files',params={'thread_id':'../'+second})
        check('invalid thread_id rejected',response.status_code==422,str(response.status_code))
        server._sessions.clear()


def sql():
    for statement in ['DROP TABLE drugs','DELETE FROM drugs','UPDATE drugs SET drug_id=2','INSERT INTO drugs VALUES (1)',
                      'TRUNCATE TABLE drugs','SELECT 1; DROP TABLE drugs',"SELECT * FROM drugs INTO OUTFILE '/tmp/x'"]:
        try: db._validated_select(statement,'pharma_db',200);check(f'SQL rejects {statement!r}',False)
        except db.QueryRejected: check(f'SQL rejects {statement!r}',True)
    check('SQL allows plain SELECT',isinstance(db._validated_select('SELECT drug_id FROM drugs','pharma_db',200),db.exp.Select))


def secrets():
    tracked=subprocess.run(['git','ls-files','-z','-co','--exclude-standard'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.split('\0')
    hits=[]
    for name in filter(None,tracked):
        path=ROOT/name
        if not path.is_file() or path.suffix in ('.lock','.pdf','.png') or path.stat().st_size>2_000_000: continue
        try: text=path.read_text(encoding='utf-8')
        except UnicodeDecodeError: continue
        hits+=[f'{name}:{n}' for n,line in enumerate(text.splitlines(),1) if SECRET.search(line)]
    check('no hardcoded keys in tracked/unignored files',not hits,', '.join(hits[:5]))
    for name in ['.env','.env.stage1.json','.env.stage57-budget.json']:
        ignored=subprocess.run(['git','check-ignore','-q',name],cwd=ROOT).returncode==0
        check(f'{name} is git-ignored',ignored)
    example=(ROOT/'.env.example').read_text(encoding='utf-8')
    check('.env.example keys are empty',all(not line.split('=',1)[1].strip() for line in example.splitlines() if re.match(r'\w*(KEY|PASSWORD)=',line)))


def prints():
    found=[]
    for entry in RUNTIME:
        for path in ([ROOT/entry] if entry.endswith('.py') else (ROOT/entry).rglob('*.py')):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,ast.Call) and getattr(node.func,'id',None)=='print': found.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    check('no print() in runtime modules',not found,', '.join(found[:5]))


if __name__=='__main__':
    paths();sql();secrets();prints()
    print(f'{sum(results)}/{len(results)} PASS')
    raise SystemExit(0 if all(results) else 1)
