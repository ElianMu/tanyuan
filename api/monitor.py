"""事件上报 + 连接管理（TDD §4.1 / §8.2）。"""
from __future__ import annotations

import json
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

    async def connect(self, thread_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(thread_id, []).append(ws)

    def disconnect(self, thread_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(thread_id)
        if conns is None:
            return
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self._connections.pop(thread_id, None)

    async def send_event(
        self, thread_id: str, event: str, message: str, data: dict | None = None
    ) -> None:
        """把事件推送到指定会话的所有连接。

        ensure_ascii=True 让 JSON 走纯 ASCII（中文转 \\uXXXX），
        规避 Vite 的 WS 代理对非 ASCII 帧的 UTF-8 转写问题。
        """
        text = json.dumps(build_event(event, message, data), ensure_ascii=True)
        for ws in list(self._connections.get(thread_id, [])):
            try:
                await ws.send_text(text)
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
        tool_name = serialized.get("name", "")
        args = inputs or {}
        if tool_name == "task":
            subtype = args.get("subagent_type", "")
            await monitor.report(
                self._thread_id,
                "assistant_call",
                f"调度子智能体：{subtype}",
                {"assistant_name": subtype, "args": args.get("description", "")},
            )
        else:
            await monitor.report(
                self._thread_id,
                "tool_start",
                f"调用工具：{tool_name}",
                {"tool_name": tool_name, "args": args},
            )
