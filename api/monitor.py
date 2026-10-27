"""事件上报 + 连接管理（TDD §4.1 / §8.2）。"""
from __future__ import annotations

import json
import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from langchain_core.callbacks import AsyncCallbackHandler


def build_event(event: str, message: str, data: dict[str, Any] | None = None) -> dict:
    """按 TDD §8.2 组装统一推送结构。"""
    return {
        "type": "monitor_event",
        "event": event,
        "message": message,
        "data": data or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class ConnectionManager:
    """按会话（thread_id）路由的 WebSocket 连接管理（TDD §4.1 / §8.2）。"""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}
        self._history = OrderedDict()
        self._sequence = {}
        self._send_locks = {}

    async def connect(self, thread_id: str, ws: WebSocket, after: int | None = None) -> None:
        if sum(map(len, self._connections.values())) >= 100 or len(self._connections.get(thread_id, [])) >= 4:
            await ws.close(code=1013, reason="连接数量超过限制")
            return False
        await ws.accept()
        lock = asyncio.Lock()
        self._send_locks[ws] = lock
        try:
            async with lock:
                self._connections.setdefault(thread_id, []).append(ws)
                if after is not None:
                    for event in list(self._history.get(thread_id, [])):
                        if event["seq"] > after:
                            await asyncio.wait_for(ws.send_text(json.dumps(event, ensure_ascii=True)), timeout=2)
        except Exception:
            self.disconnect(thread_id, ws)
            raise
        return True

    def disconnect(self, thread_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(thread_id)
        if conns is None:
            return
        if ws in conns:
            conns.remove(ws)
        self._send_locks.pop(ws, None)
        if not conns:
            self._connections.pop(thread_id, None)

    async def send_event(
        self, thread_id: str, event: str, message: str, data: dict | None = None
    ) -> None:
        """把事件推送到指定会话的所有连接。

        ensure_ascii=True 让 JSON 走纯 ASCII（中文转 \\uXXXX），
        规避 Vite 的 WS 代理对非 ASCII 帧的 UTF-8 转写问题。
        """
        payload = build_event(event, message, data)
        payload["thread_id"] = thread_id
        payload["seq"] = self._sequence.get(thread_id, 0) + 1
        self._sequence[thread_id] = payload["seq"]
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 32768:
            payload["data"] = {"truncated": True, "summary": "事件数据过长，完整结果请查看任务状态"}
        if thread_id not in self._history:
            if len(self._history) >= 100:
                expired, _ = self._history.popitem(last=False)
                self._sequence.pop(expired, None)
            self._history[thread_id] = deque(maxlen=128)
        self._history[thread_id].append(payload)
        text = json.dumps(payload, ensure_ascii=True)
        for ws in list(self._connections.get(thread_id, [])):
            try:
                async with self._send_locks[ws]:
                    await asyncio.wait_for(ws.send_text(text), timeout=2)
            except Exception:  # noqa: BLE001
                self.disconnect(thread_id, ws)


class ToolMonitor:
    """事件上报单例（TDD §4.1）。"""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def report(
        self, thread_id: str, event: str, message: str, data: dict | None = None
    ) -> None:
        await self._manager.send_event(thread_id, event, message, data)


# 模块级单例：全项目共享同一份连接管理与事件上报
manager = ConnectionManager()
monitor = ToolMonitor(manager)


class MonitorCallbackHandler(AsyncCallbackHandler):
    """把主智能体的工具调用事件实时上报（TDD §8.2）。

    - 工具名为 ``task`` → 判定为子智能体调度（assistant_call）
    - 其他工具 → 普通工具调用（tool_start）
    """

    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id
        self._parents = {}
        self._tools = {}

    async def on_chain_start(self, serialized, inputs, *, run_id=None, parent_run_id=None, **kwargs):
        self._parents[str(run_id)] = str(parent_run_id) if parent_run_id else None

    def _parent_tool(self, parent):
        visited = set()
        while parent and parent not in visited:
            if parent in self._tools: return parent
            visited.add(parent)
            parent = self._parents.get(parent)
        return None

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        tool_name = (serialized or {}).get("name", "")
        args = inputs or {}
        node_id = str(run_id)
        parent = str(parent_run_id) if parent_run_id else None
        self._parents[node_id] = parent
        self._tools[node_id] = tool_name
        linkage = {"id": node_id, "parent_id": self._parent_tool(parent), "tool_name": tool_name}
        if tool_name == "task":
            subtype = args.get("subagent_type", "")
            await monitor.report(
                self._thread_id,
                "assistant_call",
                f"调度子智能体：{subtype}",
                {**linkage, "assistant_name": subtype, "args": args.get("description", "")},
            )
        else:
            await monitor.report(
                self._thread_id,
                "tool_start",
                f"调用工具：{tool_name}",
                {**linkage, "args": args},
            )

    async def on_tool_end(self, output, *, run_id=None, **kwargs):
        text = str(getattr(output, "content", output))
        await monitor.report(self._thread_id, "tool_end", "工具返回", {
            "id": str(run_id), "tool_name": self._tools.get(str(run_id), ""), "result": text[:2000],
            "status": getattr(output, "status", "success"),
        })

    async def on_tool_error(self, error, *, run_id=None, **kwargs):
        await monitor.report(self._thread_id, "tool_error", "工具执行失败", {"id": str(run_id), "status": "error"})
