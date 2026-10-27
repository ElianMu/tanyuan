"""Strict relative paths for session file tools; no virtual traversal cleanup."""
from pathlib import Path
from utils import path_utils


def resolve_file(root: Path, value: str) -> Path:
    if (not value or value.startswith('/') or any(c in value for c in ('\\', ':', '%', '\x00'))
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise path_utils.PathOutOfBoundsError("文件路径必须是合法会话相对路径")
    candidate = root
    for part in value.split('/'):
        candidate = candidate / part
        if candidate.is_symlink():
            raise path_utils.PathOutOfBoundsError("文件路径不允许符号链接")
    return path_utils.resolve_within(root, value)
