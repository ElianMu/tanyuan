"""Explicit, local-only stage 1 setup and real MySQL acceptance.

Never loads .env. Initialization refuses every existing schema. Reader credentials
must be injected by the operator; this script never prints them or changes root.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import sys
import stat
import unittest
from decimal import Decimal
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mysql.connector
from tools import mysql_tools as db

SCHEMA = "pharma_stage1_test"
READER = "tanyuan_stage1_reader"


def admin_connection():
    # A password, if required, comes from process environment, never argv.
    return mysql.connector.connect(
        host="127.0.0.1", port=3306, user="root",
        password=os.environ.get("TANYUAN_TEST_ADMIN_PASSWORD", ""),
        use_pure=True, charset="utf8mb4", autocommit=True,
        connection_timeout=5, read_timeout=15, write_timeout=15,
    )


def initialize():
    from sqlglot import parse
    source = (ROOT / "sql/init.sql").read_text()
    prefix = "CREATE DATABASE IF NOT EXISTS pharma_db DEFAULT CHARSET utf8mb4;\nUSE pharma_db;"
    if not source.startswith(prefix):
        raise RuntimeError("Initialization header changed; manual review required")
    # No global text substitution: only the two reviewed schema statements vary.
    script = f"CREATE DATABASE `{SCHEMA}` DEFAULT CHARSET utf8mb4;\nUSE `{SCHEMA}`;" + source[len(prefix):]
    statements = parse(script, read="mysql")
    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (SCHEMA,))
            if cur.fetchone()[0]:
                raise RuntimeError("Test schema already exists; refusing to initialize or alter it")
            for statement in statements:
                cur.execute(statement.sql(dialect="mysql"))
        print("PASS: created isolated test schema and original seeds (no existing schema touched)")
    finally:
        conn.close()


def create_reader():
    password = os.environ.get("MYSQL_PASSWORD", "")
    if len(password) < 24 or os.environ.get("MYSQL_USER") != READER:
        raise RuntimeError("Inject a dedicated reader and a random password of at least 24 characters")
    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM mysql.user WHERE User=%s", (READER,))
            if cur.fetchone()[0]:
                raise RuntimeError("Reader already exists; refusing to modify account or credentials")
            cur.execute(f"CREATE USER '{READER}'@'127.0.0.1' IDENTIFIED BY %s WITH MAX_USER_CONNECTIONS 3", (password,))
            for table in db.TABLES:
                cur.execute(f"GRANT SELECT ON `{SCHEMA}`.`{table}` TO '{READER}'@'127.0.0.1'")
        print("PASS: created dedicated three-table SELECT account; password not printed")
    finally:
        conn.close()


class RealMySQLTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get("MYSQL_HOST"), "127.0.0.1")
        self.assertEqual(os.environ.get("MYSQL_DATABASE"), SCHEMA)
        self.assertEqual(os.environ.get("MYSQL_USER"), READER)

    def rows(self, sql):
        result = db.execute_sql_query(sql)
        self.assertFalse(result.startswith("连接数据库失败"), result)
        return list(csv.reader(io.StringIO(result)))

    def test_schema_seeds_and_foreign_keys(self):
        conn = admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (SCHEMA,))
                self.assertEqual([r[0] for r in cur], list(db.TABLES))
                cur.execute("SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME='drugs'", (SCHEMA,))
                self.assertEqual(cur.fetchone(), (2,))
            for table, count in zip(db.TABLES, [10, 30, 20]):
                self.assertEqual(self.rows(f"SELECT COUNT(*) FROM {table}")[1], [str(count)])
        finally:
            conn.close()

    def test_three_public_tools_and_join(self):
        self.assertEqual(db.list_sql_tables(), "可用的表：drugs,inventory,sales_records")
        for table, count in zip(db.TABLES, [10, 30, 20]):
            self.assertEqual(len(list(csv.reader(io.StringIO(db.get_table_data(table))))) - 1, count)
        rows = self.rows("SELECT d.drug_id, COUNT(s.sale_id), SUM(s.total_amount) FROM drugs d JOIN sales_records s ON d.drug_id=s.drug_id WHERE d.drug_id > 0 GROUP BY d.drug_id ORDER BY d.drug_id")
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[1], ["1", "2", "17250.00"])

    def test_decimal_types_values_round_and_warnings(self):
        queries = [
            ("0.0000001", Decimal("0.0000001")),
            ("-0.0000001", Decimal("-0.0000001")),
            ("ROUND(0.00000025, 7)", Decimal("0.0000003")),
            ("0.00000000", Decimal("0.00000000")),
            ("1.0000000", Decimal("1.0000000")),
            ("9223372036854775807.0 + 1", Decimal("9223372036854775808.0")),
            ("0.0000001 + 0.0000002", Decimal("0.0000003")),
            ("9" * 35 + "." + "9" * 30, Decimal("9" * 35 + "." + "9" * 30)),
            ("0." + "0" * 29 + "1", Decimal("0." + "0" * 29 + "1")),
        ]
        with db._cursor() as cur:
            self.assertIsInstance(cur._connection.converter, db.FixedPointMySQLConverter)
            for expression, expected in queries:
                with self.subTest(expression=expression):
                    statement, params = db._bound_sql(db._validated_select("SELECT " + expression, SCHEMA, 200))
                    cur.execute(statement, params)
                    value = cur.fetchone()[0]
                    self.assertIsInstance(value, Decimal)
                    self.assertEqual(value, expected)
                    self.assertIsNone(cur.fetchone())
                    cur.execute("SHOW WARNINGS")
                    self.assertEqual(list(cur), [])
        self.assertEqual(self.rows("SELECT SUM(total_amount) / COUNT(*) FROM sales_records")[1], ["107660.000000"])

    def test_connection_modes_permissions_and_roles(self):
        with db._cursor() as cur:
            cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), @@SESSION.sql_mode, @@character_set_connection, @@SESSION.max_execution_time, @@SESSION.transaction_read_only")
            user, role, modes, charset, timeout, readonly = cur.fetchone()
            self.assertEqual(user, READER + "@127.0.0.1")
            self.assertEqual(role, "NONE")
            self.assertEqual(set(modes.split(",")), db.SUPPORTED_SQL_MODES)
            self.assertEqual((charset, timeout, readonly), ("utf8mb4", 10000, 1))
            self.assertIsNone(cur.fetchone())
            cur.execute("SHOW GRANTS")
            grants = [row[0] for row in cur]
            self.assertEqual(len(grants), 4)
            self.assertEqual(sum("GRANT USAGE ON *.*" in g for g in grants), 1)
            for table in db.TABLES:
                self.assertEqual(sum(f"GRANT SELECT ON `{SCHEMA}`.`{table}` TO" in g for g in grants), 1)
        admin = admin_connection()
        try:
            with admin.cursor() as cur:
                for table, column in [("role_edges", "TO_USER"), ("default_roles", "USER"), ("proxies_priv", "User")]:
                    cur.execute(f"SELECT COUNT(*) FROM mysql.{table} WHERE `{column}`=%s", (READER,))
                    self.assertEqual(cur.fetchone(), (0,))
        finally:
            admin.close()

    def test_server_timeout_and_connection_cleanup(self):
        connection_ids = []
        original = db._get_connection
        def tracked():
            conn = original()
            connection_ids.append(conn.connection_id)
            return conn
        with patch.object(db, "_get_connection", side_effect=tracked):
            self.rows("SELECT 1")
            with patch.dict(os.environ, {"MYSQL_QUERY_TIMEOUT_MS": "1"}):
                # Read-only bounded by server deadline; no SLEEP, writes or test data changes.
                sql = "SELECT SUM(a.total_amount) FROM sales_records a " + " ".join(
                    f"JOIN sales_records {alias} ON {alias}.drug_id > 0" for alias in "bcdefg")
                self.assertIn("超时", db.execute_sql_query(sql))
        admin = admin_connection()
        try:
            with admin.cursor() as cur:
                for connection_id in connection_ids:
                    cur.execute("SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE ID=%s", (connection_id,))
                    self.assertEqual(cur.fetchone(), (0,))
        finally:
            admin.close()

    def test_rejected_inputs_do_not_connect(self):
        with patch.object(db.mysql.connector, "connect", side_effect=AssertionError("must not connect")) as connect:
            for sql in ["SELECT 1e-7", "SELECT 1.", "SELECT SLEEP(1)", "SELECT * FROM mysql.user", "SELECT 1; SELECT 2", "DELETE FROM drugs", "SELECT * FROM drugs FOR UPDATE"]:
                self.assertTrue(db.execute_sql_query(sql).startswith("连接数据库失败："))
            connect.assert_not_called()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--initialize-new-test-schema", action="store_true")
    action.add_argument("--create-reader", action="store_true")
    parser.add_argument("--credentials-file", type=Path,
                        help="Explicit local JSON credentials, must be a regular 0600 file")
    args = parser.parse_args()
    try:
        if args.credentials_file:
            path = args.credentials_file
            if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise RuntimeError("Credentials must be a regular 0600 file, never a symlink")
            values = json.loads(path.read_text())
            if (set(values) != {"MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"}
                    or not all(isinstance(value, str) for value in values.values())
                    or values["MYSQL_HOST"] != "127.0.0.1" or values["MYSQL_PORT"] != "3306"
                    or values["MYSQL_USER"] != READER or values["MYSQL_DATABASE"] != SCHEMA):
                raise RuntimeError("Credentials must target the dedicated local test identity")
            os.environ.update(values)
        if args.initialize_new_test_schema:
            initialize()
        elif args.create_reader:
            create_reader()
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealMySQLTests)
            raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
    except Exception as exc:
        # Never print raw driver exceptions that may include account details.
        print("FAIL:", str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        raise SystemExit(1)
