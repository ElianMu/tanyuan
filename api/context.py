"""会话上下文（TDD §4.1）：ContextVar 会话隔离。

用 ContextVar 保存会话工作目录与线程标识，任务生命周期内 set/reset，
避免 asyncio 多协程下全局变量串台。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional
from pathlib import Path

from utils import path_utils

_thread_id: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)
_session_dir: ContextVar[Optional[str]] = ContextVar("session_dir", default=None)


def set_session(thread_id: str, session_dir: str) -> None:
    """设置当前协程的会话上下文。"""
    expected = path_utils.get_session_dir(thread_id)
    if Path(session_dir).resolve() != expected:
        raise path_utils.PathOutOfBoundsError("上下文目录与会话 ID 不匹配")
    _thread_id.set(thread_id)
    _session_dir.set(str(expected))


def get_thread_id() -> Optional[str]:
    """获取当前会话标识。"""
    return _thread_id.get()


def get_session_dir() -> Optional[str]:
    """获取当前会话工作目录（绝对路径）。"""
    saved = _session_dir.get()
    if saved is not None:
        expected = path_utils.get_session_dir(_thread_id.get())
        if Path(saved).resolve() != expected:
            raise path_utils.PathOutOfBoundsError("会话上下文目录已改变")
        return str(expected)
    return None


def reset_session() -> None:
    """重置当前会话上下文。"""
    _thread_id.set(None)
    _session_dir.set(None)
