"""阶段 1 离线单测。禁止真实网络和 .env 读取；所有连接均为 Fake。"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _offline_guard(event, args):
    if event == 'socket.connect':
        raise RuntimeError('数据库单测禁止真实网络连接')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        if Path(args[0]).name == '.env':
            raise RuntimeError('数据库单测禁止读取 .env')


sys.addaudithook(_offline_guard)
from tools import mysql_tools as db
from mysql.connector.constants import ClientFlag
from decimal import Decimal, localcontext
from mysql.connector.connection import MySQLConnection
from mysql.connector.conversion import MySQLConverter
from mysql.connector.cursor import MySQLCursor, _ParamSubstitutor, RE_PY_PARAM
import sqlglot

ENV = {
    'MYSQL_HOST': 'localhost', 'MYSQL_PORT': '3306', 'MYSQL_USER': 'test_reader',
    'MYSQL_PASSWORD': 'fake-test-value', 'MYSQL_DATABASE': 'pharma_db',
}


class FakeCursor:
    def __init__(self, rows=(), columns=('value',)):
        self.rows = iter(rows)
        self.description = [(name,) for name in columns]
        self.executed = []
        self.closed = False
        self.execute_error = self.fetch_error = self.close_error = None
        self.fetch_count = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.execute_error:
            raise self.execute_error

    def fetchone(self):
        self.fetch_count += 1
        if self.fetch_error:
            raise self.fetch_error
        return next(self.rows, None)

    def fetchall(self):
        raise AssertionError('禁止 fetchall')

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeConnection:
    def __init__(self):
        self.setup = FakeCursor([(10000,), (','.join(sorted(db.SUPPORTED_SQL_MODES)),)])
        self.query = FakeCursor([(1,)])
        self.cursors = []
        self.closed = False
        self.version = (8, 4, 0)
        self.info = '8.4.0'
        self.cursor_error_at = None
        self.close_error = None

    def get_server_version(self):
        return self.version

    def get_server_info(self):
        return self.info

    def cursor(self, **kwargs):
        if self.cursor_error_at == len(self.cursors):
            raise RuntimeError('fake cursor failure')
        result = self.setup if not self.cursors else self.query
        self.cursors.append(result)
        return result

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class MySQLToolTests(unittest.TestCase):
    def setUp(self):
        env_patch = patch.dict(os.environ, ENV, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.conn = FakeConnection()
        connector_patch = patch.object(db.mysql.connector, 'connect', return_value=self.conn)
        self.connect = connector_patch.start()
        self.addCleanup(connector_patch.stop)

    def query_result(self, rows, columns=('value',)):
        self.conn.query = FakeCursor(rows, columns)

    def assert_closed(self):
        self.assertTrue(self.conn.closed)
        for cursor in self.conn.cursors:
            self.assertTrue(cursor.closed)

    def test_list_tables_and_no_tables(self):
        self.query_result([('drugs',), ('inventory',), ('sales_records',)])
        self.assertEqual(db.list_sql_tables(), '可用的表：drugs,inventory,sales_records')
        sql, params = self.conn.query.executed[0]
        self.assertIn('TABLE_SCHEMA = %s', sql)
        self.assertEqual(params, ('pharma_db', 'drugs', 'inventory', 'sales_records'))
        self.assert_closed()
        self.conn = FakeConnection()
        self.connect.return_value = self.conn
        self.query_result([])
        self.assertEqual(db.list_sql_tables(), '没有可用的表')
        self.assert_closed()

    def test_preview_csv_escaping_and_limit(self):
        self.query_result([(1, '中文,逗号\n换行"引号')], ('id', 'text'))
        result = db.get_table_data('drugs')
        self.assertEqual(list(csv.reader(io.StringIO(result))), [['id', 'text'], ['1', '中文,逗号\n换行"引号']])
        self.assertEqual(self.conn.query.executed, [('SELECT * FROM `drugs` LIMIT %s', (100,))])
        self.assert_closed()

    def test_preview_empty_and_invalid_table(self):
        self.query_result([])
        self.assertEqual(db.get_table_data('inventory'), '查询表inventory,结果没有数据')
        self.assert_closed()
        self.connect.reset_mock()
        for name in ['drugs\n', 'mysql.user', 'secrets', 'drugs`', '', None]:
            with self.subTest(name=name):
                self.assertTrue(db.get_table_data(name).startswith('非法的表名：'))
        self.connect.assert_not_called()

    def test_select_join_group_order_and_aggregate(self):
        queries = [
            'SELECT * FROM drugs',
            'SELECT COUNT(*) FROM inventory',
            'SELECT COUNT(DISTINCT drug_id), SUM(total_amount), AVG(total_amount), MIN(total_amount), MAX(total_amount) FROM sales_records',
            "SELECT d.generic_name, SUM(s.total_amount) AS total FROM pharma_db.drugs d JOIN sales_records s ON d.drug_id=s.drug_id WHERE s.sale_date >= '2025-01-01' GROUP BY d.generic_name HAVING SUM(s.total_amount) > 0 ORDER BY total DESC LIMIT 10",
            'SELECT d.drug_id FROM drugs d LEFT JOIN inventory i ON d.drug_id=i.drug_id WHERE i.drug_id IS NULL',
            'SELECT drug_id FROM drugs WHERE drug_id IN (1,2) AND drug_id BETWEEN 1 AND 5',
            'SELECT ROUND(AVG(total_amount), 2), COALESCE(SUM(total_amount), 0) FROM sales_records',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.conn = FakeConnection()
                self.connect.return_value = self.conn
                self.assertEqual(db.execute_sql_query(sql), 'value\n1')
                self.assert_closed()
                normalized, params = self.conn.query.executed[0]
                self.assertIn('LIMIT %s', normalized)
                self.assertEqual(normalized.count('%s'), len(params))

    def test_illegal_sql_rejected_before_connection_or_execute(self):
        queries = [
            '', 'not sql', 'SELECT (', 'SELECT FROM',
            'INSERT INTO drugs VALUES (1)', 'UPDATE drugs SET drug_id=2',
            'DELETE FROM drugs', 'DROP TABLE drugs', 'REPLACE INTO drugs VALUES (1)',
            'CALL example()', 'SET @x=1', 'CREATE TABLE x(id INT)', 'GRANT ALL ON *.* TO x',
            'SELECT 1; SELECT 2', 'SELECT 1;;',
            "SELECT * FROM drugs INTO OUTFILE '/tmp/never-write'",
            "SELECT * FROM drugs INTO DUMPFILE '/tmp/never-write'",
            'WITH x AS (SELECT * FROM drugs) SELECT * FROM x',
            'SELECT * FROM drugs WHERE drug_id IN (SELECT drug_id FROM inventory)',
            'SELECT (SELECT 1) FROM drugs', 'SELECT * FROM (SELECT * FROM drugs) d',
            'SELECT * FROM drugs UNION SELECT * FROM inventory',
            'SELECT * FROM drugs FOR UPDATE', 'SELECT * FROM drugs LOCK IN SHARE MODE',
            'SELECT SQL_CALC_FOUND_ROWS * FROM drugs',
            'SELECT SLEEP(30)', 'SELECT BENCHMARK(1000,1)', 'SELECT GET_LOCK(\'x\',1)',
            "SELECT LOAD_FILE('/etc/passwd')", 'SELECT evil_function(drug_id) FROM drugs',
            'SELECT @@version', 'SELECT @x:=1', 'SELECT @x',
            'SELECT * FROM other.drugs', 'SELECT * FROM mysql.user', 'SELECT * FROM secrets',
            'SELECT other.drugs.drug_id FROM drugs', 'SELECT catalog.pharma_db.drugs.drug_id FROM drugs',
            'SELECT * FROM drugs CROSS JOIN inventory', 'SELECT * FROM drugs JOIN inventory',
            'SELECT * FROM drugs LIMIT -1', 'SELECT * FROM drugs LIMIT 1 OFFSET 100001',
            'SELECT * FROM drugs WHERE drug_id = ?', 'SELECT * FROM drugs USE INDEX (x)',
            'SELECT SUM(total_amount) OVER () FROM sales_records', 'SELECT * FROM drugs PROCEDURE ANALYSE()',
            'SELECT `a.b` FROM drugs', 'SELECT * FROM drugs; /* tail */ SELECT 1',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assertTrue(db.execute_sql_query(sql).startswith('连接数据库失败：'))
                self.connect.assert_not_called()
                self.assertEqual(self.conn.setup.executed, [])
                self.assertEqual(self.conn.query.executed, [])

    def test_literal_binding_order_and_injection_string(self):
        sql = "SELECT 'first', 2 FROM drugs WHERE generic_name = 'x; DROP TABLE drugs' AND drug_id > 3 LIMIT 4 OFFSET 5"
        self.assertEqual(db.execute_sql_query(sql), 'value\n1')
        normalized, params = self.conn.query.executed[0]
        self.assertNotIn('DROP TABLE', normalized)
        self.assertEqual(params, ('first', Decimal(2), 'x; DROP TABLE drugs', Decimal(3), Decimal(4), Decimal(5)))
        self.assertEqual(normalized.count('%s'), len(params))

    def test_missing_configuration_and_admin_rejected(self):
        for key in ['MYSQL_HOST', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DATABASE']:
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                self.assertIn('连接数据库失败：', db.list_sql_tables())
                self.connect.assert_not_called()
        for user in ['root', 'ROOT', ' root ', 'mysql.sys']:
            with self.subTest(user=user), patch.dict(os.environ, MYSQL_USER=user):
                self.assertIn('禁止管理员账号', db.get_table_data('drugs'))
                self.connect.assert_not_called()

    def test_invalid_config_ranges_and_charset(self):
        invalid = {'MYSQL_PORT': 'no', 'MYSQL_CONNECT_TIMEOUT': '0', 'MYSQL_READ_TIMEOUT': '61',
                   'MYSQL_WRITE_TIMEOUT': '-1', 'MYSQL_QUERY_TIMEOUT_MS': '30001',
                   'MYSQL_MAX_ROWS': '1001', 'MYSQL_MAX_RESULT_BYTES': '4194305',
                   'MYSQL_DATABASE': 'mysql', 'MYSQL_CHARSET': 'latin1'}
        for key, value in invalid.items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                self.assertTrue(db.get_table_data('drugs').startswith('连接数据库失败：'))
                self.connect.assert_not_called()

    def test_connection_configuration_and_server_deadline(self):
        self.assertEqual(db.execute_sql_query('SELECT 1'), 'value\n1')
        options = self.connect.call_args.kwargs
        for key, value in {'charset':'utf8mb4', 'connection_timeout':5, 'read_timeout':15,
                           'write_timeout':15, 'allow_local_infile':False, 'autocommit':True}.items():
            self.assertEqual(options[key], value)
        self.assertFalse(options['client_flags'] & ClientFlag.MULTI_STATEMENTS)
        self.assertFalse(options['client_flags'] & ClientFlag.LOCAL_FILES)
        self.assertIs(options['converter_class'], db.FixedPointMySQLConverter)
        self.assertEqual(self.conn.setup.executed, [
            ('SET SESSION max_execution_time = %s', (10000,)),
            ('SELECT @@SESSION.max_execution_time', None),
            ('SELECT @@SESSION.sql_mode', None),
            ('SET SESSION TRANSACTION READ ONLY', None),
        ])
        self.assert_closed()

    def test_server_version_and_setup_failure_fail_closed(self):
        for version, info in [((5,7,44),'5.7.44'), ((9,0,0),'9.0.0'), ((8,0,0),'8.0.0-MariaDB')]:
            with self.subTest(version=version):
                self.conn = FakeConnection(); self.connect.return_value = self.conn
                self.conn.version = version; self.conn.info = info
                self.assertIn('仅支持', db.list_sql_tables())
                self.assertTrue(self.conn.closed)
                self.assertEqual(self.conn.query.executed, [])
        for mode in ['setup_error', 'wrong_deadline']:
            self.conn = FakeConnection(); self.connect.return_value = self.conn
            if mode == 'setup_error': self.conn.setup.execute_error = RuntimeError('sensitive details')
            else: self.conn.setup.rows = iter([(0,)])
            self.assertTrue(db.list_sql_tables().startswith('连接数据库失败：'))
            self.assert_closed()
            self.assertEqual(self.conn.query.executed, [])

    def test_connection_failure_returns_sanitized_error(self):
        self.connect.side_effect = RuntimeError('fake-password-and-private-host')
        for tool in [db.list_sql_tables, lambda:db.get_table_data('drugs'), lambda:db.execute_sql_query('SELECT 1')]:
            result = tool()
            self.assertTrue(result.startswith('连接数据库失败：'))
            self.assertNotIn('fake-password', result)

    def test_query_and_fetch_timeouts_release_resources(self):
        for where in ['execute_error', 'fetch_error']:
            with self.subTest(where=where):
                self.conn = FakeConnection(); self.connect.return_value = self.conn
                setattr(self.conn.query, where, TimeoutError('fake-sensitive'))
                self.assertIn('超时', db.execute_sql_query('SELECT * FROM drugs'))
                self.assert_closed()
        self.conn = FakeConnection(); self.connect.return_value = self.conn
        self.conn.query.execute_error = db.mysql.connector.Error(msg='private', errno=3024)
        self.assertIn('超时', db.get_table_data('drugs'))
        self.assert_closed()
        for error_type in (db.ReadTimeoutError, db.WriteTimeoutError):
            self.conn = FakeConnection(); self.connect.return_value = self.conn
            self.conn.query.execute_error = error_type()
            self.assertIn('超时', db.execute_sql_query('SELECT 1'))
            self.assert_closed()

    def test_cursor_creation_and_close_errors_release_connection(self):
        for index in [0,1]:
            self.conn = FakeConnection(); self.connect.return_value = self.conn
            self.conn.cursor_error_at = index
            self.assertTrue(db.list_sql_tables().startswith('连接数据库失败：'))
            self.assertTrue(self.conn.closed)
        self.conn = FakeConnection(); self.connect.return_value = self.conn
        self.conn.query.close_error = RuntimeError('close failure')
        self.conn.close_error = RuntimeError('close failure')
        self.assertEqual(db.execute_sql_query('SELECT 1'), 'value\n1')
        self.assert_closed()

    def test_row_cap_clamp_and_no_fetchall(self):
        with patch.dict(os.environ, MYSQL_MAX_ROWS='2'):
            self.query_result([(1,), (2,)])
            self.assertEqual(db.execute_sql_query('SELECT * FROM drugs LIMIT 999'), 'value\n1\n2')
            self.assertEqual(self.conn.query.executed[0][1], (Decimal(2),))
            self.assert_closed()
            self.conn = FakeConnection(); self.connect.return_value = self.conn
            self.query_result([(1,), (2,), (3,), (4,)])
            self.assertIn('行数超过', db.execute_sql_query('SELECT * FROM drugs'))
            self.assertEqual(self.conn.query.fetch_count, 3)
            self.assert_closed()

    def test_byte_cap_with_multibyte_and_header(self):
        for rows, columns in [([('中文',)], ('x',)), ([(1,)], ('long_header',))]:
            self.conn = FakeConnection(); self.connect.return_value = self.conn
            self.query_result(rows, columns)
            with patch.dict(os.environ, MYSQL_MAX_RESULT_BYTES='5'):
                self.assertIn('字节限制', db.execute_sql_query('SELECT * FROM drugs'))
            self.assert_closed()

    def test_empty_custom_query_and_input_size(self):
        self.query_result([])
        self.assertEqual(db.execute_sql_query('SELECT 1'), '执行语句SELECT 1,结果没有数据')
        self.assert_closed()
        self.connect.reset_mock()
        for query in [None, 'SELECT ' + 'x' * 17000, "SELECT '" + 'x' * 4097 + "'"]:
            self.assertTrue(db.execute_sql_query(query).startswith('连接数据库失败：'))
            self.connect.assert_not_called()

    def test_comments_cannot_override_deadline_or_execute_payload(self):
        self.assertEqual(db.execute_sql_query('SELECT /* harmless */ * FROM drugs -- comment'), 'value\n1')
        self.assertNotIn('comment', self.conn.query.executed[0][0])
        self.connect.reset_mock()
        result = db.execute_sql_query('SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM drugs')
        self.assertTrue(result.startswith('连接数据库失败：'))
        self.connect.assert_not_called()

    def driver_wire_sql(self, statement, parameters, converter_class=db.FixedPointMySQLConverter):
        # 真实驱动的转换和占位符处理，仅构造未连接的对象。
        connection = MySQLConnection()
        # 走驱动实际 config -> converter_class 路径，不替换 _process_params。
        connection.config(charset='utf8mb4', converter_class=converter_class)
        self.assertIsInstance(connection.converter, converter_class)
        connection._sql_mode = ','.join(sorted(db.SUPPORTED_SQL_MODES))
        cursor = MySQLCursor(connection)
        substitute = _ParamSubstitutor(cursor._process_params(parameters))
        result = RE_PY_PARAM.sub(substitute, statement.encode('utf-8'))
        self.assertEqual(substitute.remaining, 0)
        return result.decode('utf-8')

    def test_review_null_order_extensions_rejected_before_connection(self):
        for ordering in ['NULLS FIRST', 'NULLS LAST', 'NULLS /* gap */ LAST', 'nulls first']:
            for direction in ['', ' DESC']:
                query = 'SELECT 5 FROM drugs ORDER BY COALESCE(drug_id, 3)' + direction + ' ' + ordering
                with self.subTest(query=query):
                    self.assertIn('不支持 NULLS', db.execute_sql_query(query))
                    self.connect.assert_not_called()
                    self.assertEqual(self.conn.setup.executed, [])
                    self.assertEqual(self.conn.query.executed, [])
        # 字符串里的同名文本不被误认为排序扩展。
        self.assertEqual(db.execute_sql_query("SELECT 'NULLS LAST' FROM drugs"), 'value\n1')

    def test_review_generator_refuses_expression_expansion(self):
        tree = db._validated_select('SELECT 5 FROM drugs ORDER BY COALESCE(drug_id, 3)', 'pharma_db', 200)
        tree.find(sqlglot.exp.Ordered).set('nulls_first', False)
        with self.assertRaises(db.QueryRejected):
            db._bound_sql(tree)
        self.connect.assert_not_called()
        # 即便将来生成器错误重复了文本，也不能将不匹配的参数交给驱动。
        with patch.object(sqlglot.exp.Select, 'sql', return_value='SELECT %s, %s'):
            with self.assertRaises(db.QueryRejected):
                db._bound_sql(db._validated_select('SELECT 1', 'pharma_db', 200))

    def test_review_native_order_division_and_parameter_order(self):
        queries = [
            'SELECT 5 FROM drugs ORDER BY COALESCE(drug_id, 3) ASC LIMIT 7 OFFSET 2',
            'SELECT 5 FROM drugs ORDER BY COALESCE(drug_id, 3) DESC LIMIT 7 OFFSET 2',
            'SELECT SUM(total_amount) / COUNT(*) FROM sales_records',
            'SELECT 1 / 2, 7 % 3, (8 - 2) / (3 + 1)',
            'SELECT ROUND(1.25, 1), 7 / COALESCE(quantity_on_hand, 2) FROM inventory '
            'WHERE quantity_on_hand > 3 GROUP BY quantity_on_hand HAVING SUM(quantity_on_hand) > 4 '
            'ORDER BY COALESCE(quantity_on_hand, 5) LIMIT 6 OFFSET 8',
        ]
        for query in queries:
            with self.subTest(query=query):
                tree = db._validated_select(query, 'pharma_db', 200)
                expected = tree.sql(dialect='mysql')
                statement, parameters = db._bound_sql(tree)
                wire = self.driver_wire_sql(statement, parameters)
                self.assertEqual(sqlglot.parse_one(wire, read='mysql').sql(dialect='mysql'), expected)
                self.assertNotIn('CASE WHEN', statement)
                self.assertNotIn('NULLIF', statement)
                self.assertEqual(db.execute_sql_query(query), 'value\n1')
                self.conn = FakeConnection(); self.connect.return_value = self.conn
        query = queries[-1]
        statement, parameters = db._bound_sql(db._validated_select(query, 'pharma_db', 200))
        self.assertEqual(parameters, (Decimal('1.25'), 1, 7, 2, 3, 4, 5, 6, 8))

    def test_review_exact_numeric_types_and_scientific_rejection(self):
        query = 'SELECT 7, 1.25, -2, -0.50, ROUND(2.5), ROUND(-2.5, 0)'
        statement, parameters = db._bound_sql(db._validated_select(query, 'pharma_db', 200))
        self.assertEqual(parameters, (7, Decimal('1.25'), 2, Decimal('0.50'), Decimal('2.5'), Decimal('2.5'), 0, 200))
        self.assertEqual([type(p) for p in parameters], [int, Decimal, int, Decimal, Decimal, Decimal, int, int])
        self.assertIn('ROUND(-2.5, 0)', self.driver_wire_sql(statement, parameters))
        for query in ['SELECT ROUND(25E-1)', 'SELECT 1e3', 'SELECT -1E-3',
                      'SELECT 1.2e+4 FROM drugs', 'SELECT ROUND(AVG(total_amount), 1e0) FROM sales_records']:
            with self.subTest(query=query):
                self.assertIn('近似数值', db.execute_sql_query(query))
                self.connect.assert_not_called()
        self.assertEqual(db.execute_sql_query('SELECT ROUND(AVG(total_amount), 2) FROM sales_records'), 'value\n1')

    def test_review_ambiguous_quotes_and_backslashes_rejected(self):
        for query in ['SELECT "generic_name" FROM drugs', "SELECT 'a" + chr(92) + "nb'",
                      'SELECT `a"b` FROM drugs', "SELECT 'a" + chr(92) + chr(92) + "b'"]:
            with self.subTest(query=query):
                self.assertIn('双引号或反斜杠', db.execute_sql_query(query))
                self.connect.assert_not_called()
        statement, params = db._bound_sql(db._validated_select("SELECT 'O''Brien' FROM drugs", 'pharma_db', 200))
        self.assertEqual(params[0], "O'Brien")
        self.driver_wire_sql(statement, params)

    def test_review_sql_modes_fail_closed_and_cleanup(self):
        supported = ','.join(sorted(db.SUPPORTED_SQL_MODES))
        for modes in ['ANSI_QUOTES', 'NO_BACKSLASH_ESCAPES', supported + ',ANSI_QUOTES',
                      supported + ',NO_BACKSLASH_ESCAPES', supported + ',PIPES_AS_CONCAT', '', None]:
            with self.subTest(modes=modes):
                self.conn = FakeConnection(); self.connect.return_value = self.conn
                self.conn.setup.rows = iter([(10000,), (modes,)])
                self.assertIn('SQL Mode', db.execute_sql_query('SELECT * FROM drugs'))
                self.assertEqual(self.conn.query.executed, [])
                self.assert_closed()
        self.conn = FakeConnection(); self.connect.return_value = self.conn
        # 顺序和大小写不影响模式集合比较，没有 SET GLOBAL 或静默改写会话模式。
        self.conn.setup.rows = iter([(10000,), (', '.join(reversed(sorted(db.SUPPORTED_SQL_MODES))).lower(),)])
        self.assertEqual(db.execute_sql_query('SELECT 1'), 'value\n1')
        self.assertFalse(any('sql_mode =' in sql.lower() or 'global' in sql.lower()
                             for sql, params in self.conn.setup.executed))

    def test_review_division_attributes_and_illegal_operators_stay_closed(self):
        for key, value in [('safe', False), ('typed', True), ('new_unknown_option', True)]:
            tree = sqlglot.parse_one('SELECT 1 / 2', read='mysql')
            tree.find(sqlglot.exp.Div).set(key, value)
            with patch.object(db.sqlglot, 'parse', return_value=[tree]):
                with self.assertRaises(db.QueryRejected):
                    db._validated_select('SELECT 1 / 2', 'pharma_db', 200)
        for query in ['SELECT 1 DIV 2', 'SELECT POW(2,3)', 'SELECT SLEEP(1)/2',
                      'SELECT (SELECT 1)/2', 'SELECT 1 << 2', 'SELECT 1 / GET_LOCK(\'x\',1)']:
            with self.subTest(query=query):
                self.assertTrue(db.execute_sql_query(query).startswith('连接数据库失败：'))
                self.connect.assert_not_called()

    def test_trailing_decimal_point_rejected_before_connect(self):
        queries = [
            'SELECT 1.', 'SELECT -1.', 'SELECT +1.',
            'SELECT 9223372036854775807. + 1',
            'SELECT ROUND(-1., 0)', 'SELECT 1. / 2',
            'SELECT * FROM drugs WHERE drug_id = 1.',
            'SELECT * FROM drugs ORDER BY COALESCE(drug_id, 1.)',
            'SELECT /* comment */ -1. -- trailing comment\n',
        ]
        for query in queries:
            with self.subTest(query=query):
                self.assertIn('尾随小数点', db.execute_sql_query(query))
                self.connect.assert_not_called()
                self.assertEqual(self.conn.setup.executed, [])
                self.assertEqual(self.conn.query.executed, [])

    def test_trailing_point_text_and_comments_are_not_numeric(self):
        queries = [
            "SELECT '1.', '-1.', '9223372036854775807.'",
            "SELECT 'O''Brien 1.', 'NULLS LAST 1.' /* 9223372036854775807. */",
            'SELECT 1 /* -1. */ -- 9223372036854775807.\n',
            'SELECT drugs.drug_id FROM drugs /* 1. */ WHERE drug_id = 2',
        ]
        for query in queries:
            with self.subTest(query=query):
                tree = db._validated_select(query, 'pharma_db', 200)
                statement, params = db._bound_sql(tree)
                wire = self.driver_wire_sql(statement, params)
                self.assertEqual(sqlglot.parse_one(wire, read='mysql').sql(dialect='mysql'),
                                 tree.sql(dialect='mysql', comments=False))
                self.assertEqual(db.execute_sql_query(query), 'value\n1')
                self.assert_closed()
                self.conn = FakeConnection(); self.connect.return_value = self.conn

    def test_exact_numeric_driver_semantics_and_scientific_still_rejected(self):
        query = 'SELECT 1, -2, .125, -0.50, 1.0, 9223372036854775807.0 + 1, ROUND(-2.50, 1)'
        tree = db._validated_select(query, 'pharma_db', 200)
        statement, params = db._bound_sql(tree)
        self.assertEqual([type(p) for p in params],
                         [int, int, Decimal, Decimal, Decimal, Decimal, int, Decimal, int, int])
        wire = self.driver_wire_sql(statement, params)
        self.assertIn('9223372036854775807.0 + 1', wire)
        self.assertEqual(sqlglot.parse_one(wire, read='mysql').sql(dialect='mysql'),
                         tree.sql(dialect='mysql'))
        self.assertEqual(db.execute_sql_query(query), 'value\n1')
        self.connect.reset_mock()
        for query in ['SELECT 1e0', 'SELECT -1E-2', 'SELECT ROUND(25E-1)']:
            with self.subTest(query=query):
                self.assertIn('近似数值', db.execute_sql_query(query))
                self.connect.assert_not_called()

    def test_decimal_required_boundary_queries_use_fixed_numeric_tokens(self):
        queries = [
            'SELECT 0.0000001', 'SELECT -0.0000001', 'SELECT 0.00000025',
            'SELECT ROUND(0.00000025, 7)', 'SELECT 0.00000000',
            'SELECT 1.0000000', 'SELECT 9223372036854775807.0',
            'SELECT 0.0000001 + 0.0000002',
            'SELECT SUM(total_amount) / COUNT(*) FROM sales_records',
        ]
        for query in queries:
            with self.subTest(query=query):
                tree = db._validated_select(query, 'pharma_db', 200)
                statement, params = db._bound_sql(tree)
                wire = self.driver_wire_sql(statement, params)
                self.assertEqual(sqlglot.parse_one(wire, read='mysql').sql(dialect='mysql'),
                                 tree.sql(dialect='mysql'))
                for token in sqlglot.tokenize(wire, read='mysql'):
                    if token.token_type is db.TokenType.NUMBER:
                        self.assertNotIn('e', token.text.lower())
                self.assertEqual(db.execute_sql_query(query), 'value\n1')
                self.assertIs(self.connect.call_args.kwargs['converter_class'], db.FixedPointMySQLConverter)
                self.assert_closed()
                self.conn = FakeConnection(); self.connect.return_value = self.conn

    def test_decimal_mixed_parameter_order_types_and_no_numeric_quotes(self):
        query = "SELECT 3, -4, 0.0000001, -0.00000025, '1E-7', ROUND(1.0000000, 2)"
        statement, params = db._bound_sql(db._validated_select(query, 'pharma_db', 200))
        self.assertEqual(params, (3, 4, Decimal('0.0000001'), Decimal('0.00000025'),
                                  '1E-7', Decimal('1.0000000'), 2, 200))
        self.assertEqual([type(v) for v in params], [int, int, Decimal, Decimal, str, Decimal, int, int])
        self.assertEqual(self.driver_wire_sql(statement, params),
                         "SELECT 3, -4, 0.0000001, -0.00000025, '1E-7', ROUND(1.0000000, 2) LIMIT 200")

    def test_decimal_converter_is_connection_scoped_and_context_independent(self):
        value = Decimal('0.0000001')
        statement, params = db._bound_sql(db._validated_select('SELECT 0.0000001', 'pharma_db', 200))
        self.assertEqual(self.driver_wire_sql(statement, params, MySQLConverter), 'SELECT 1E-7 LIMIT 200')
        with localcontext() as ctx:
            ctx.prec = 2
            query = 'SELECT 123456789.123456789, 0.0000001'
            s, p = db._bound_sql(db._validated_select(query, 'pharma_db', 200))
            self.assertEqual(self.driver_wire_sql(s, p), query + ' LIMIT 200')
        self.assertEqual(db.FixedPointMySQLConverter().to_mysql(value), b'0.0000001')
        # 基础驱动行为保持不变，没有 monkey patch 或全局 Decimal 修改。
        self.assertEqual(MySQLConverter().to_mysql(value), b'1E-7')

    def test_decimal_converter_precision_scale_and_allocation_bounds(self):
        converter = db.FixedPointMySQLConverter()
        maximum = '9' * 35 + '.' + '9' * 30
        for value, expected in [(Decimal(maximum), maximum),
                                (Decimal('0.' + '0' * 29 + '1'), '0.' + '0' * 29 + '1'),
                                (Decimal('-0.00000000'), '-0.00000000')]:
            self.assertEqual(converter.to_mysql(value), expected.encode('ascii'))
        for value in [Decimal('NaN'), Decimal('sNaN'), Decimal('Infinity'), Decimal('-Infinity'),
                      Decimal('1E-1000000'), Decimal('1E+1000000'), Decimal('1'),
                      Decimal('9' * 36 + '.' + '9' * 30)]:
            with self.subTest(value=value), self.assertRaises(db.QueryRejected):
                converter.to_mysql(value)

    def test_decimal_unsupported_queries_rejected_before_connection(self):
        queries = ['SELECT 1e-7', 'SELECT ROUND(25E-1)', 'SELECT -1.',
                   'SELECT 0.' + '0' * 30 + '1',
                   'SELECT ' + '9' * 36 + '.' + '9' * 30,
                   'SELECT 0.' + '0' * 4097]
        for query in queries:
            with self.subTest(query=query):
                self.assertTrue(db.execute_sql_query(query).startswith('连接数据库失败：'))
                self.connect.assert_not_called()
                self.assertEqual(self.conn.setup.executed, [])
                self.assertEqual(self.conn.query.executed, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
