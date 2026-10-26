"""数据库三工具（TDD §7.2）。

- 连接配置从环境变量读取（TDD §12）：charset=utf8mb4，autocommit=True；
- 异常统一返回「连接数据库失败：{错误}」。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# 加载项目根目录的 .env（保证直接运行本脚本也能读到数据库配置）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# 写操作关键字拦截（TDD §14，仅兜底；主防线是只读账号 + 参数化查询）
_FORBIDDEN_WRITE_PATTERN = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|TRUNCATE|ALTER|CREATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

# 表名白名单：仅允许字母/数字/下划线，防 SQL 注入（TDD §14）
_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def _get_connection() -> mysql.connector.MySQLConnection:
    """按 TDD §12 建立数据库连接：utf8mb4 + autocommit。

    use_pure=True 走纯 Python 实现，规避 C 扩展在 asyncio 事件循环里的崩溃。
    """
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "pharma_db"),
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
        autocommit=True,
        use_pure=True,
    )


def list_sql_tables() -> str:
    """列出数据库所有表：有表返回「可用的表：a,b,c」，无表返回「没有可用的表」。"""
    try:
        conn = _get_connection()
    except Exception as e:  # noqa: BLE001
        return f"连接数据库失败：{e}"
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        return "没有可用的表" if not tables else "可用的表：" + ",".join(tables)
    except Exception as e:  # noqa: BLE001
        return f"连接数据库失败：{e}"
    finally:
        conn.close()


def get_table_data(table_name: str) -> str:
    """读取指定表前 100 行：首行字段名，后续每行一条记录（逗号分隔）。"""
    if not _TABLE_NAME_PATTERN.match(table_name):
        return f"非法的表名：{table_name}"
    try:
        conn = _get_connection()
    except Exception as e:  # noqa: BLE001
        return f"连接数据库失败：{e}"
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 100")
        rows = cursor.fetchall()
        if not rows:
            return f"查询表{table_name},结果没有数据"
        col_names = [desc[0] for desc in cursor.description]
        lines = [",".join(str(c) for c in col_names)]
        for row in rows:
            lines.append(",".join(str(v) for v in row))
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"连接数据库失败：{e}"
    finally:
        conn.close()


def execute_sql_query(sql: str) -> str:
    """执行自定义 SQL：返回同 get_table_data；无数据返回「执行语句{...},结果没有数据」。"""
    # 写操作关键字拦截（TDD §14 兜底，禁 DROP/DELETE/UPDATE 等）
    matched = _FORBIDDEN_WRITE_PATTERN.search(sql)
    if matched:
        return f"禁止执行写操作语句（含 {matched.group(1)}）"
    try:
        conn = _get_connection()
    except Exception as e:  # noqa: BLE001
        return f"连接数据库失败：{e}"
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        if not rows:
            return f"执行语句{sql},结果没有数据"
        col_names = [desc[0] for desc in cursor.description]
        lines = [",".join(str(c) for c in col_names)]
        for row in rows:
            lines.append(",".join(str(v) for v in row))
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"连接数据库失败：{e}"
    finally:
        conn.close()


if __name__ == "__main__":
    print("=== list_sql_tables ===")
    print(list_sql_tables())
    print("\n=== get_table_data('drugs') ===")
    print(get_table_data("drugs"))
    print("\n=== execute_sql_query('SELECT COUNT(*) AS cnt FROM drugs') ===")
    print(execute_sql_query("SELECT COUNT(*) AS cnt FROM drugs"))
