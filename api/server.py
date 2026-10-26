"""REST 路由 + WebSocket 端点 + 启动引导（TDD §4.1 / §8）。"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from api.monitor import manager, monitor
from utils import path_utils

app = FastAPI(title="探渊 · 多智能体数据分析系统")


@app.exception_handler(path_utils.InvalidSessionIDError)
async def invalid_session_id(_request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(path_utils.PathOutOfBoundsError)
async def unsafe_path(_request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=403)


class TaskRequest(BaseModel):
    query: str
    thread_id: str | None = None


@app.get("/")
def root() -> dict:
    return {"status": "ok", "message": "探渊服务已启动"}


async def _run_task(thread_id: str, query: str) -> None:
    """后台执行任务，结束后推送 task_result / error 事件。"""
    try:
        # 延迟加载业务编排，健康检查启动不读取密钥或初始化外部客户端。
        from agent.main_agent import run_deep_agent

        result = await run_deep_agent(query, thread_id)
        await monitor.report(thread_id, "task_result", "任务完成", {"result": result})
    except Exception as e:  # noqa: BLE001
        await monitor.report(thread_id, "error", f"任务失败：{e}")


@app.post("/api/task")
async def submit_task(body: TaskRequest) -> dict:
    """提交任务：创建会话目录、上报 session_created，异步执行并立即返回 thread_id。"""
    thread_id = uuid4().hex if body.thread_id is None else body.thread_id
    session_dir = path_utils.get_session_dir(thread_id)
    path_utils.get_upload_dir(thread_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    await monitor.report(
        thread_id, "session_created", "会话工作目录已创建", {"path": str(session_dir)}
    )
    # 任务异步执行（TDD §8.1）：create_task 后台跑，立即返回
    asyncio.create_task(_run_task(thread_id, body.query))
    return {"status": "started", "thread_id": thread_id}


@app.post("/api/upload")
async def upload(
    thread_id: str = Form(...), files: list[UploadFile] = File(...)
) -> dict:
    """上传文件：流式写入 upload/session_{thread_id}。"""
    upload_dir = path_utils.get_upload_dir(thread_id)
    path_utils.get_session_dir(thread_id)
    # 整批预校验，避免第二个文件非法时第一个文件已经写入。
    targets = [path_utils.resolve_upload_file(thread_id, f.filename) for f in files]
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for f, target in zip(files, targets):
        with open(target, "wb") as out:
            while chunk := await f.read(1024 * 1024):
                out.write(chunk)
        saved.append(f.filename)
    return {"status": "uploaded", "files": saved}


@app.get("/api/files")
async def list_files(path: str):
    """浏览目录：越界校验后返回目录内容（mtime 倒序，TDD §8.1）。"""
    target = path_utils.validate_path(path, path_utils.OUTPUT_DIR)
    if target is None:
        return JSONResponse({"error": "拒绝访问"}, status_code=403)
    if not target.exists() or not target.is_dir():
        return JSONResponse({"error": "路径不存在"}, status_code=404)
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        entries.append(
            {
                "name": p.name,
                "type": "dir" if p.is_dir() else "file",
                "path": str(p),
                "size": p.stat().st_size if p.is_file() else None,
                "mtime": p.stat().st_mtime,
            }
        )
    return {"files": entries}


@app.get("/api/download")
async def download(path: str):
    """下载文件：越界校验后返回文件流（TDD §8.1）。"""
    target = path_utils.validate_path(path, path_utils.OUTPUT_DIR)
    if target is None:
        return JSONResponse({"error": "拒绝访问"}, status_code=403)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(target, filename=target.name)


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(ws: WebSocket, thread_id: str) -> None:
    """WebSocket 端点：心跳（pong）+ 事件推送（TDD §8.2）。"""
    try:
        path_utils.validate_session_id(thread_id)
    except path_utils.InvalidSessionIDError:
        await ws.close(code=1008, reason="非法会话 ID")
        return
    await manager.connect(thread_id, ws)
    try:
        while True:
            data = await ws.receive_text()
            await ws.send_text(
                json.dumps(
                    {"type": "pong", "message": f"服务端已收到: {data}"},
                    ensure_ascii=True,
                )
            )
    except WebSocketDisconnect:
        manager.disconnect(thread_id, ws)


def start_server() -> None:
    """启动服务（默认监听 0.0.0.0:8000，TDD §16.2）。"""
    import uvicorn

    from utils.logger_utils import get_logger

    get_logger("tanyuan").info("正在启动探渊服务：http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
