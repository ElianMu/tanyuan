"""TDD §7.2：有界、默认拒绝的只读 MySQL 工具。

仅消费进程环境变量；导入不会读取 .env 或连接数据库。
SQL AST 白名单与数据库专用 SELECT 账号是两道独立防线。
"""
from __future__ import annotations

import csv
import io
import os
import re
from contextlib import contextmanager
from decimal import Decimal

import mysql.connector
from mysql.connector.constants import ClientFlag
from mysql.connector.conversion import MySQLConverter
from mysql.connector.errors import ReadTimeoutError, WriteTimeoutError
import sqlglot
from sqlglot import exp
from sqlglot.dialects.mysql import MySQL
from sqlglot.tokens import TokenType

TABLES = ("drugs", "inventory", "sales_records")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_SQL_BYTES = 16384
# 与 MySQL 8.x 默认模式一致；只验证当前连接，不修改全局或会话配置。
SUPPORTED_SQL_MODES = frozenset({
    "ONLY_FULL_GROUP_BY", "STRICT_TRANS_TABLES", "NO_ZERO_IN_DATE", "NO_ZERO_DATE",
    "ERROR_FOR_DIVISION_BY_ZERO", "NO_ENGINE_SUBSTITUTION",
})
_EXACT_NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


class QueryRejected(ValueError):
    pass


class ConfigurationError(ValueError):
    pass


class FixedPointMySQLConverter(MySQLConverter):
    """连接级转换器：保留 Decimal 定点文本，仍由驱动绑定数值参数。"""

    @staticmethod
    def _decimal_to_mysql(value: Decimal) -> bytes:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise QueryRejected("Decimal 参数必须为有限精确数值")
        _, digits, exponent = value.as_tuple()
        # SQL 整数使用 int；没有小数位的 Decimal 也不能悄悄变成整数。
        # 在 format 前检查，避免极大指数导致无界字符串分配。
        if exponent >= 0 or -exponent > 30 or max(len(digits), -exponent) > 65:
            raise QueryRejected("Decimal 参数须有 1–30 位小数且精度不超过 65 位")
        return format(value, "f").encode("ascii")


def _integer(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        if not 1 <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise ConfigurationError(f"配置 {name} 必须为 1–{maximum} 的整数") from None


def _schema() -> str:
    value = os.environ.get("MYSQL_DATABASE", "")
    if len(value) > 64 or not _IDENTIFIER.fullmatch(value) or value.lower() in (
        "mysql", "information_schema", "performance_schema", "sys"
    ):
        raise ConfigurationError("MYSQL_DATABASE 缺失或不是允许的业务库标识符")
    return value


def _limits() -> tuple[int, int]:
    return (_integer("MYSQL_MAX_ROWS", 200, 1000),
            _integer("MYSQL_MAX_RESULT_BYTES", 1048576, 4194304))


def _quiet_close(resource) -> None:
    if resource is not None:
        try:
            resource.close()
        except Exception:
            # 清理失败不覆盖查询结果；外层仍关闭短连接，不复用未读结果连接。
            pass


def _get_connection():
    schema = _schema()
    for key in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD"):
        if not os.environ.get(key, "").strip():
            raise ConfigurationError(f"缺少环境变量 {key}")
    if os.environ["MYSQL_USER"].strip().lower() in ("root", "mysql.sys", "mysql.session"):
        raise ConfigurationError("MYSQL_USER 必须为专用只读账号，禁止管理员账号")
    if os.environ.get("MYSQL_CHARSET", "utf8mb4").lower() != "utf8mb4":
        raise ConfigurationError("MYSQL_CHARSET 必须为 utf8mb4")
    timeout_ms = _integer("MYSQL_QUERY_TIMEOUT_MS", 10000, 30000)
    options = dict(
        host=os.environ["MYSQL_HOST"], user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"], database=schema,
        port=_integer("MYSQL_PORT", 3306, 65535), charset="utf8mb4",
        autocommit=True, use_pure=True, allow_local_infile=False,
        converter_class=FixedPointMySQLConverter,
        client_flags=ClientFlag.get_default() & ~ClientFlag.MULTI_STATEMENTS & ~ClientFlag.LOCAL_FILES,
        connection_timeout=_integer("MYSQL_CONNECT_TIMEOUT", 5, 30),
        read_timeout=_integer("MYSQL_READ_TIMEOUT", 15, 60),
        write_timeout=_integer("MYSQL_WRITE_TIMEOUT", 15, 60),
    )
    conn = cursor = None
    try:
        conn = mysql.connector.connect(**options)
        # 仅声明支持 Oracle MySQL 8.x；其他版本默认拒绝。
        version = conn.get_server_version()
        if version[:1] != (8,) or "mariadb" in conn.get_server_info().lower():
            raise ConfigurationError("仅支持已声明的 Oracle MySQL 8.x，需先完成版本验收")
        cursor = conn.cursor(buffered=False)
        # 固定内部语句，不接受用户 SET；值使用驱动参数绑定。
        cursor.execute("SET SESSION max_execution_time = %s", (timeout_ms,))
        cursor.execute("SELECT @@SESSION.max_execution_time")
        if cursor.fetchone() != (timeout_ms,):
            raise ConfigurationError("数据库端查询时限设置验证失败")
        cursor.execute("SELECT @@SESSION.sql_mode")
        mode_row = cursor.fetchone()
        if (not mode_row or not isinstance(mode_row[0], str)
                or {mode.strip().upper() for mode in mode_row[0].split(",")}
                != SUPPORTED_SQL_MODES):
            raise ConfigurationError("数据库会话 SQL Mode 与支持模式不一致")
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        return conn
    except Exception:
        _quiet_close(cursor)
        cursor = None
        _quiet_close(conn)
        raise
    finally:
        _quiet_close(cursor)


@contextmanager
def _cursor():
    conn = cursor = None
    try:
        conn = _get_connection()
        cursor = conn.cursor(buffered=False)
        yield cursor
    finally:
        _quiet_close(cursor)
        _quiet_close(conn)


# 精确类型及参数白名单：未知表达式、未知选项、未知函数一律拒绝。
_NODE_ARGS = {
    exp.Select: {"expressions", "from_", "joins", "where", "group", "having", "order", "limit", "offset", "distinct"},
    exp.From: {"this"}, exp.Table: {"this", "db", "alias"},
    exp.Identifier: {"this", "quoted"}, exp.TableAlias: {"this"},
    exp.Column: {"this", "table", "db"}, exp.Star: set(),
    exp.Alias: {"this", "alias"}, exp.Literal: {"this", "is_string"},
    exp.Null: set(), exp.Boolean: {"this"}, exp.Distinct: {"expressions"},
    exp.Where: {"this"}, exp.Having: {"this"}, exp.Group: {"expressions"},
    exp.Order: {"expressions"}, exp.Ordered: {"this", "desc", "nulls_first"},
    exp.Limit: {"expression"}, exp.Offset: {"expression"},
    exp.Join: {"this", "on", "side", "kind"},
    exp.In: {"this", "expressions"}, exp.Between: {"this", "low", "high"},
    exp.Count: {"this", "expressions", "big_int"},
    exp.Sum: {"this"}, exp.Avg: {"this"}, exp.Min: {"this"}, exp.Max: {"this"},
    exp.Coalesce: {"this", "expressions"}, exp.Round: {"this", "decimals"},
}
for _type in (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
              exp.And, exp.Or, exp.Is, exp.Like, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod):
    _NODE_ARGS[_type] = {"this", "expression"}
for _type in (exp.Not, exp.Neg, exp.Paren):
    _NODE_ARGS[_type] = {"this"}
_NODE_ARGS[exp.Div] = {"this", "expression", "typed", "safe"}


def _validated_select(sql: str, schema: str, maximum_rows: int) -> exp.Select:
    if not isinstance(sql, str) or not sql.strip() or len(sql.encode("utf-8")) > _MAX_SQL_BYTES:
        raise QueryRejected("SQL 为空或超过 16 KiB")
    # 保守子集：不猜测 ANSI_QUOTES / NO_BACKSLASH_ESCAPES 下的含义。
    # 包括字符串及注释中的这些字符；标准单引号翻倍转义仍可用。
    if '"' in sql or "\\" in sql:
        raise QueryRejected("不允许双引号或反斜杠语法")
    try:
        # 提前拒绝非 SELECT，避免解析器 Command 回退日志记录用户 SQL。
        # 这只是入口约束，后面仍必须执行完整 AST 白名单校验。
        tokens = sqlglot.tokenize(sql, read="mysql")
        if not tokens or tokens[0].token_type is not TokenType.SELECT:
            raise QueryRejected("仅允许单条 SELECT")
        # 只检查数值词法单元；字符串和注释中的 1. 不是 SQL 数值。
        # Decimal('1.') 会被驱动输出为整数 1，因此在解析/连接前拒绝。
        if any(token.token_type is TokenType.NUMBER
               and re.fullmatch(r"[0-9]+\.", token.text) for token in tokens):
            raise QueryRejected("不支持尾随小数点的数值字面量")
        # AST 不保留显式 NULLS FIRST 与默认排序的区别，须检查非字符串词法单元。
        for current, following in zip(tokens, tokens[1:]):
            if (current.token_type not in (TokenType.STRING, TokenType.IDENTIFIER)
                    and following.token_type not in (TokenType.STRING, TokenType.IDENTIFIER)
                    and current.text.upper() == "NULLS"
                    and following.text.upper() in ("FIRST", "LAST")):
                raise QueryRejected("MySQL 安全子集不支持 NULLS FIRST/LAST")
        statements = sqlglot.parse(sql, read="mysql", error_level=sqlglot.ErrorLevel.RAISE)
    except QueryRejected:
        raise
    except Exception:
        raise QueryRejected("SQL 无法按 MySQL 语法安全解析") from None
    if len(statements) != 1 or type(statements[0]) is not exp.Select:
        raise QueryRejected("仅允许单条 SELECT")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > 1000:
        raise QueryRejected("SQL 结构过于复杂")
    for node in nodes:
        allowed = _NODE_ARGS.get(type(node))
        if allowed is None or any(value not in (None, False, [], "") and key not in allowed
                                  for key, value in node.args.items()):
            raise QueryRejected("SQL 包含未允许的结构、函数或选项")
        if isinstance(node, exp.Select) and node is not tree:
            raise QueryRejected("不允许嵌套 SELECT 或 CTE")
        if isinstance(node, exp.Identifier) and not _IDENTIFIER.fullmatch(node.this):
            raise QueryRejected("SQL 标识符不合法")
        if isinstance(node, exp.Table):
            if node.name not in TABLES or (node.db and node.db != schema):
                raise QueryRejected("仅允许配置库中的三张业务表")
        if isinstance(node, exp.Column) and node.db and node.db != schema:
            raise QueryRejected("不允许跨库列引用")
        if isinstance(node, exp.Join):
            if not node.args.get("on") or node.args.get("kind", "").upper() not in ("", "INNER"):
                raise QueryRejected("JOIN 必须有 ON，且仅允许 INNER/LEFT/RIGHT JOIN")
            if node.args.get("side", "").upper() not in ("", "LEFT", "RIGHT"):
                raise QueryRejected("不支持该 JOIN 类型")
        if isinstance(node, exp.Div):
            if node.args.get("typed") is not False or node.args.get("safe") is not True:
                raise QueryRejected("仅允许 MySQL 原生普通除法")
        if isinstance(node, exp.Ordered):
            if node.args.get("nulls_first") is not (not bool(node.args.get("desc"))):
                raise QueryRejected("不允许模拟 NULL 排序")
        if isinstance(node, exp.Literal):
            if len(node.this) > 4096:
                raise QueryRejected("SQL 常量过大")
            if not node.is_string and not _EXACT_NUMBER.fullmatch(node.this):
                raise QueryRejected("不支持科学计数法或其他近似数值")
        node.comments = None  # 不执行原文注释或优化器 hint。
    for key, bound in (("limit", maximum_rows), ("offset", 100000)):
        clause = tree.args.get(key)
        if clause is None:
            continue
        literal = clause.expression
        if type(literal) is not exp.Literal or literal.is_string or not re.fullmatch(r"[0-9]+", literal.this):
            raise QueryRejected("LIMIT/OFFSET 必须为非负整数字面量")
        value = int(literal.this)
        if key == "offset" and value > bound:
            raise QueryRejected("OFFSET 过大")
        clause.set("expression", exp.Literal.number(min(value, bound)))
    if tree.args.get("limit") is None:
        tree = tree.limit(maximum_rows)
    return tree


def _bound_sql(tree: exp.Select) -> tuple[str, tuple]:
    """在生成器输出顺序收集常量，避免 AST 遍历顺序与占位符顺序不一致。"""
    parameters = []

    def literal_sql(generator, node):
        if node.is_string:
            value = node.this
        else:
            if not _EXACT_NUMBER.fullmatch(node.this):
                raise QueryRejected("不支持科学计数法或其他近似数值")
            value = Decimal(node.this) if "." in node.this else int(node.this)
            if isinstance(value, Decimal):
                # 与连接级转换器共用边界，在建立连接前拒绝不可安全表示的数值。
                FixedPointMySQLConverter._decimal_to_mysql(value)
        parameters.append(value)
        return "%s"

    class BoundMySQL(MySQL):
        class Generator(MySQL.Generator):
            TRANSFORMS = {**MySQL.Generator.TRANSFORMS, exp.Literal: literal_sql}

            def ordered_sql(self, expression):
                # 不委托会重复使用已生成字符串的 NULL 排序模拟器。
                desc = expression.args.get("desc")
                if expression.args.get("nulls_first") is not (not bool(desc)):
                    raise QueryRejected("不允许模拟 NULL 排序")
                order = " DESC" if desc else (" ASC" if desc is False else "")
                return self.sql(expression, "this") + order

            def div_sql(self, expression):
                if expression.args.get("typed") is not False or expression.args.get("safe") is not True:
                    raise QueryRejected("仅允许 MySQL 原生普通除法")
                # MySQL 本身处理除零，不插入 NULLIF、CAST 或重复操作数。
                return self.binary(expression, "/")

    statement = tree.sql(dialect=BoundMySQL, comments=False,
                         unsupported_level=sqlglot.ErrorLevel.RAISE)
    # 最终生成文本的词法防线：不统计原 SQL，也不替换字符串或丢弃参数。
    # 已拒绝所有需要表达式展开的扩展；若生成器未来变化，必须默认失败。
    tokens = sqlglot.tokenize(statement, read="mysql")
    occurrences = sum(
        left.token_type is TokenType.MOD and right.token_type is TokenType.VAR
        and right.text == "s" and right.start == left.end + 1
        for left, right in zip(tokens, tokens[1:])
    )
    if occurrences != len(parameters):
        raise QueryRejected("SQL 生成后的占位符与参数不一致")
    return statement, tuple(parameters)


def _error(exc: Exception) -> str:
    # 不转发驱动原文，避免连接地址、凭据、SQL 内容出现在异常返回中。
    if isinstance(exc, (ConfigurationError, QueryRejected)):
        detail = str(exc)
    elif isinstance(exc, (TimeoutError, ReadTimeoutError, WriteTimeoutError)) or getattr(exc, "errno", None) in (3024, 1317, 1205):
        detail = "数据库查询或网络操作超时"
    else:
        detail = "数据库操作失败，请检查服务、只读权限和超时配置"
    return f"连接数据库失败：{detail}"


def _csv_line(values, maximum_bytes: int) -> str:
    texts = []
    size = 0
    for value in values:
        text = str(value)
        # 在 CSV 转义产生额外副本前拒绝超大单元格和行。
        if len(text) > maximum_bytes:
            raise QueryRejected("查询结果超过字节限制")
        size += len(text.encode("utf-8"))
        if size > maximum_bytes:
            raise QueryRejected("查询结果超过字节限制")
        texts.append(text)
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerow(texts)
    return output.getvalue()[:-2]


def _read_csv(cursor, maximum_rows: int, maximum_bytes: int) -> str:
    header = _csv_line((column[0] for column in cursor.description), maximum_bytes)
    chunks = [header]
    size = len(header.encode("utf-8"))
    if size > maximum_bytes:
        raise QueryRejected("查询结果超过字节限制")
    count = 0
    while True:
        row = cursor.fetchone()
        if row is None:
            break
        if count >= maximum_rows:
            raise QueryRejected("数据库返回行数超过限制")
        line = _csv_line(row, maximum_bytes)
        size += 1 + len(line.encode("utf-8"))
        if size > maximum_bytes:
            raise QueryRejected("查询结果超过字节限制，请缩小筛选范围")
        chunks.append(line)
        count += 1
    return "\n".join(chunks) if count else ""


def list_sql_tables() -> str:
    """仅列出当前账号可见的三张业务表，保留 TDD 返回格式。"""
    try:
        schema = _schema()
        with _cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN (%s, %s, %s) "
                "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME LIMIT 3",
                (schema, *TABLES),
            )
            tables = []
            for _ in TABLES:
                row = cursor.fetchone()
                if row is None:
                    break
                if row[0] in TABLES:
                    tables.append(row[0])
        return "可用的表：" + ",".join(tables) if tables else "没有可用的表"
    except Exception as exc:
        return _error(exc)


def get_table_data(table_name: str) -> str:
    """预览指定业务表前 100 行。表标识符使用白名单，数量使用参数绑定。"""
    if not isinstance(table_name, str) or not _IDENTIFIER.fullmatch(table_name) or table_name not in TABLES:
        return "非法的表名：仅允许 drugs、inventory、sales_records"
    try:
        maximum_rows, maximum_bytes = _limits()
        with _cursor() as cursor:
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT %s", (min(100, maximum_rows),))
            result = _read_csv(cursor, min(100, maximum_rows), maximum_bytes)
        return result or f"查询表{table_name},结果没有数据"
    except Exception as exc:
        return _error(exc)


def execute_sql_query(sql: str) -> str:
    """验证后仅执行规范化、参数化的单条业务 SELECT，最多返回配置上限行。"""
    try:
        maximum_rows, maximum_bytes = _limits()
        tree = _validated_select(sql, _schema(), maximum_rows)
        statement, parameters = _bound_sql(tree)
        with _cursor() as cursor:
            cursor.execute(statement, parameters)
            result = _read_csv(cursor, maximum_rows, maximum_bytes)
        return result or f"执行语句{sql},结果没有数据"
    except Exception as exc:
        return _error(exc)
