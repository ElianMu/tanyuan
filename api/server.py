"""Local REST/WebSocket server with bounded task lifecycle and session files."""
from __future__ import annotations
import asyncio,json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI,File,Form,UploadFile,WebSocket,WebSocketDisconnect,HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field,field_validator
from api.monitor import manager,monitor
from utils import path_utils
from utils.file_utils import resolve_file


@dataclass
class Session:
    root: Path
    status: str='created'
    result: str=''


_sessions: dict[str,Session]={}
_tasks: dict[str,asyncio.Task]={}
FRONTEND_DIST=Path(__file__).resolve().parent.parent/'frontend'/'dist'


@asynccontextmanager
async def lifespan(app):
    yield
    pending=[task for task in _tasks.values() if not task.done()]
    for task in pending: task.cancel()
    if pending: await asyncio.gather(*pending,return_exceptions=True)


app=FastAPI(title='探渊 · 多智能体数据分析系统',lifespan=lifespan)


@app.exception_handler(path_utils.InvalidSessionIDError)
async def invalid_session_id(_request,exc): return JSONResponse({'detail':str(exc)},status_code=422)


@app.exception_handler(path_utils.PathOutOfBoundsError)
async def unsafe_path(_request,exc): return JSONResponse({'detail':str(exc)},status_code=403)


class TaskRequest(BaseModel):
    query: str=Field(min_length=1,max_length=12000)
    thread_id: str | None=None
    @field_validator('query')
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError('问题不能为空')
        return value.strip()


def _session(thread_id,create=False):
    root=path_utils.get_session_dir(thread_id)
    path_utils.get_upload_dir(thread_id)
    state=_sessions.get(thread_id)
    if state and state.root!=root: state=None
    if state is None and create:
        if len(_sessions)>=100: raise HTTPException(429,'本地会话数量已达上限')
        root.mkdir(parents=True,exist_ok=True)
        state=Session(root);_sessions[thread_id]=state
    if state is None: raise HTTPException(404,'会话不存在')
    return state


@app.get('/')
def root():
    if (FRONTEND_DIST/'index.html').is_file():
        return FileResponse(FRONTEND_DIST/'index.html',headers={'Cache-Control':'no-cache'})
    return health()


@app.get('/api/health')
def health(): return {'status':'ok','message':'探渊服务已启动'}


@app.post('/api/session')
async def create_session():
    thread=uuid4().hex;state=_session(thread,create=True)
    await monitor.report(thread,'session_created','会话已创建，可先上传文件',{'path':str(state.root)})
    return {'thread_id':thread,'status':state.status,'path':str(state.root)}


@app.get('/api/task/{thread_id}')
async def task_status(thread_id: str):
    state=_session(thread_id)
    return {'thread_id':thread_id,'status':state.status,'result':state.result}


async def _run_task(thread_id: str,query: str):
    state=_session(thread_id)
    try:
        from agent.main_agent import run_deep_agent
        result=await asyncio.wait_for(run_deep_agent(query,thread_id),timeout=300)
        state.result=result
        state.status='incomplete' if '【未达标说明】' in result else 'completed'
        await monitor.report(thread_id,'task_result','任务结束',{'result':result,'status':state.status})
    except asyncio.CancelledError:
        state.status='cancelled'
        await monitor.report(thread_id,'cancelled','任务已终止')
        raise
    except TimeoutError:
        state.status='failed';await monitor.report(thread_id,'error','任务超时',{'status':'failed'})
    except Exception:
        state.status='failed';await monitor.report(thread_id,'error','任务执行失败，请检查服务配置、调用额度或数据源',{'status':'failed'})


@app.post('/api/task')
async def submit_task(body: TaskRequest):
    thread=body.thread_id if body.thread_id is not None else uuid4().hex
    state=_session(thread,create=True)
    existing=_tasks.get(thread)
    if existing and not existing.done(): raise HTTPException(409,'该会话任务正在运行')
    if state.status not in ('created','running'): raise HTTPException(409,'请创建新会话提交新任务')
    if sum(not task.done() for task in _tasks.values())>=3: raise HTTPException(429,'最多同时运行三个任务')
    state.status='running'
    await monitor.report(thread,'session_created','会话工作目录已创建',{'path':str(state.root)})
    await monitor.report(thread,'task_started','任务已受理',{'status':'running'})
    _tasks[thread]=asyncio.create_task(_run_task(thread,body.query))
    return {'status':'started','thread_id':thread}


@app.post('/api/task/{thread_id}/cancel')
async def cancel_task(thread_id: str):
    state=_session(thread_id);task=_tasks.get(thread_id)
    if task and not task.done():
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        state.status='cancelled'
    return {'thread_id':thread_id,'status':state.status}


@app.post('/api/upload')
async def upload(thread_id: str=Form(...),files: list[UploadFile]=File(...)):
    folder=path_utils.get_upload_dir(thread_id);path_utils.get_session_dir(thread_id)
    targets=[path_utils.resolve_upload_file(thread_id,item.filename) for item in files]
    if len(files)>10 or len(set(targets))!=len(targets): raise HTTPException(422,'每批最多十个不同文件')
    if any(len(target.name.encode('utf-8'))>255 for target in targets): raise HTTPException(422,'文件名过长')
    if any(target.suffix.lower() not in {'.txt','.md','.pdf','.docx','.xlsx','.xls','.csv'} for target in targets): raise HTTPException(415,'不支持的上传类型')
    if any(target.exists() for target in targets): raise HTTPException(409,'文件已存在，禁止覆盖')
    task=_tasks.get(thread_id)
    if task and not task.done(): raise HTTPException(409,'请先上传，再启动任务')
    payloads=[];total=0
    try:
        for item in files:
            chunks=[];size=0
            while chunk:=await item.read(1024*1024):
                size+=len(chunk);total+=len(chunk)
                if size>10*1024*1024 or total>20*1024*1024: raise HTTPException(413,'单文件最多 10 MiB、每批最多 20 MiB')
                chunks.append(chunk)
            payloads.append(b''.join(chunks))
    finally:
        for item in files: await item.close()
    folder.mkdir(parents=True,exist_ok=True)
    for target,payload in zip(targets,payloads):
        with target.open('xb') as stream: stream.write(payload)
    return {'status':'uploaded','files':[target.name for target in targets]}


def _file_target(thread_id,path):
    base=path_utils.get_session_dir(thread_id)
    if path:
        target=path_utils.validate_path(path,base) if Path(path).is_absolute() else resolve_file(base,path)
        if target is None: raise HTTPException(403,'拒绝访问其他会话或目录')
        relative=target.relative_to(base)
        probe=base
        for part in relative.parts:
            probe=probe/part
            if probe.is_symlink(): raise HTTPException(403,'拒绝符号链接')
        return target
    return base


@app.get('/api/files')
async def list_files(thread_id: str,path: str=''):
    target=_file_target(thread_id,path)
    if not target.is_dir(): raise HTTPException(404,'目录不存在')
    entries=[]
    for item in sorted(target.iterdir(),key=lambda p:p.lstat().st_mtime,reverse=True):
        if item.is_symlink(): continue
        info=item.stat()
        entries.append({'name':item.name,'type':'dir' if item.is_dir() else 'file','path':str(item),'size':info.st_size if item.is_file() else None,'mtime':info.st_mtime})
    return {'files':entries}


@app.get('/api/download')
async def download(thread_id: str,path: str):
    target=_file_target(thread_id,path)
    if not target.is_file(): raise HTTPException(404,'文件不存在')
    return FileResponse(target,filename=target.name,headers={'X-Content-Type-Options':'nosniff'})


@app.websocket('/ws/{thread_id}')
async def websocket_endpoint(ws: WebSocket,thread_id: str,after: int | None=None):
    try: path_utils.validate_session_id(thread_id)
    except path_utils.InvalidSessionIDError:
        await ws.close(code=1008,reason='非法会话 ID');return
    try:
        if not await manager.connect(thread_id,ws,after=after): return
    except (TimeoutError,RuntimeError,WebSocketDisconnect): return
    try:
        while True:
            data=await ws.receive_text()
            if len(data)>1000: await ws.close(code=1009);break
            await ws.send_text(json.dumps({'type':'pong','message':'服务端已收到: '+data},ensure_ascii=True))
    except WebSocketDisconnect: pass
    finally: manager.disconnect(thread_id,ws)


class FrontendFiles(StaticFiles):
    async def __call__(self,scope,receive,send):
        if scope['type']=='websocket':
            await send({'type':'websocket.close','code':1008})
            return
        await super().__call__(scope,receive,send)

    async def get_response(self,path,scope):
        # Unknown API/WS URLs must never resolve to a frontend file.
        if path.split('/')[0] in {'api','ws'}:
            raise HTTPException(404,'Not Found')
        return await super().get_response(path,scope)


# Registered last so REST and WebSocket routes retain precedence. No build is
# required for backend-only operation; restart after building for deployment.
if FRONTEND_DIST.is_dir():
    app.mount('/',FrontendFiles(directory=FRONTEND_DIST,html=True),name='frontend')


def start_server():
    import uvicorn
    from utils.logger_utils import get_logger
    get_logger('tanyuan').info('正在启动探渊服务：http://127.0.0.1:8000')
    uvicorn.run(app,host='127.0.0.1',port=8000)
