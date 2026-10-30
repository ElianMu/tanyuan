"""路径解析与越界防护（TDD §4.4）。

统一处理路径相关职责：
1. 虚拟路径清洗（``clean_virtual_path``）：将用户/智能体传入的路径视作
   「相对工作目录的虚拟路径」，剥离绝对路径语义与 ``..`` 穿越成分；
2. 上传/工作目录处理（``get_session_dir`` / ``get_upload_dir``）：
   基于项目根目录解析会话工作目录与上传目录；
3. 嵌套防护 + 越界校验（``resolve_within`` / ``is_within`` / ``validate_path``）：
   确保最终落点仍位于允许的根目录内，防止路径穿越。
"""
from __future__ import annotations

import re
from pathlib import Path

# 项目根目录 = 本文件父目录的父目录（backend 根，含 pyproject.toml）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 运行时目录（TDD §3 / §9.3）
OUTPUT_DIR = PROJECT_ROOT / "output"      # 各会话工作目录
UPLOAD_DIR = PROJECT_ROOT / "upload"      # 各会话上传目录


class PathOutOfBoundsError(ValueError):
    """路径越界异常：目标路径落在允许的根目录之外。"""


class InvalidSessionIDError(ValueError):
    """会话 ID 不符合现有 UUID、命名测试会话格式。"""


def validate_session_id(thread_id: str) -> str:
    """只接受 ASCII 字母、数字、下划线及连字符，不解码或清洗非法输入。"""
    if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
        raise InvalidSessionIDError("非法会话 ID：仅允许 1–128 位字母、数字、下划线或连字符")
    return thread_id


def _session_path(root: Path, thread_id: str) -> Path:
    validate_session_id(thread_id)
    root = Path(root)
    # 根目录自身和会话目录均不能被符号链接重定向。
    if root.is_symlink():
        raise PathOutOfBoundsError("会话根目录不能是符号链接")
    try:
        base = root.resolve()
        candidate = base / f"session_{thread_id}"
        if candidate.is_symlink():
            raise PathOutOfBoundsError("会话目录不能是符号链接")
        target = candidate.resolve()
        target.relative_to(base)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathOutOfBoundsError("会话目录越界或无法安全解析") from exc
    return target


def get_session_dir(thread_id: str) -> Path:
    """会话工作目录：output/session_{thread_id}。"""
    return _session_path(OUTPUT_DIR, thread_id)


def get_upload_dir(thread_id: str) -> Path:
    """会话上传目录：upload/session_{thread_id}。"""
    return _session_path(UPLOAD_DIR, thread_id)


def resolve_upload_file(thread_id: str, filename: str) -> Path:
    """上传只允许单个文件名，并拒绝已有符号链接，不把穿越输入清洗成合法文件名。"""
    root = get_upload_dir(thread_id)
    if (not isinstance(filename, str) or filename in ("", ".", "..")
            or any(c in filename for c in ('/', '\\', ':', '%', '\x00'))):
        raise PathOutOfBoundsError("非法上传文件名")
    candidate = root / filename
    if candidate.is_symlink():
        raise PathOutOfBoundsError("上传目标不能是符号链接")
    target = candidate.resolve()
    if not is_within(target, root):
        raise PathOutOfBoundsError("上传目标越界")
    return target


def clean_virtual_path(virtual_path: str) -> str:
    """清洗虚拟路径（TDD §4.4「虚拟路径清洗」）。

    规则：
    - 统一 ``\\`` → ``/``；
    - 剥离 Windows 盘符（``C:``）与 UNC 前缀（``//server/share``）；
    - 剥离首部 ``/``（消除绝对路径语义）；
    - 丢弃 ``.`` / ``..`` / 空段（消除向上穿越）。

    返回清洗后的相对路径（纯 ``/`` 分隔，不含越界成分）。
    """
    if not virtual_path:
        return ""
    p = virtual_path.replace("\\", "/").strip()
    p = re.sub(r"^[a-zA-Z]:", "", p)      # 去盘符 C:
    p = p.lstrip("/")                      # 去绝对路径前缀
    segments = p.split("/")
    cleaned = [s for s in segments if s not in ("", ".", "..")]
    return "/".join(cleaned)


def is_within(path: Path, root: Path) -> bool:
    """越界校验：``path`` 是否位于 ``root`` 目录内（含 root 自身）。"""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_within(root: Path, virtual_path: str) -> Path:
    """清洗并解析相对路径，落点必须位于 ``root`` 内（嵌套防护 + 越界校验）。

    先 ``clean_virtual_path`` 清洗虚拟路径，再拼接 ``root`` 并解析为绝对路径；
    若最终落点不在 ``root`` 内，抛 :class:`PathOutOfBoundsError`。
    """
    cleaned = clean_virtual_path(virtual_path)
    target = (Path(root) / cleaned).resolve()
    if not is_within(target, root):
        raise PathOutOfBoundsError(f"路径越界：{virtual_path!r} 落在允许目录之外")
    return target


def validate_path(path: str, root: Path) -> Path | None:
    """校验外部传入的（绝对或相对）路径是否位于 ``root`` 内。

    越界返回 ``None``，合法返回解析后的绝对路径。用于 /api/files、/api/download
    等对外接口的越界校验（TDD §8.1 / §14）。
    """
    target = Path(path).resolve()
    return target if is_within(target, root) else None
