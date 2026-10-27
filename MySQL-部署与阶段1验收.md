# 探渊 · MySQL 部署与阶段 1 验收

依据根目录 PRD、TDD §7.2/§9.1/§12–14 和开发步骤阶段 1。本文提供后续部署步骤，本次没有安装、启动或连接真实 MySQL，也没有执行任何初始化或授权 SQL。

## 环境与账号边界

- Python 3.12.15；当前锁定 mysql-connector-python 26.10.0、SQLGlot 30.25.0。
- SQLGlot 约束 `>=30.25,<31`，使用 MySQL dialect，并逐个验证 AST 类型及参数；解析成功不等于安全。
- 当前声明支持 Oracle MySQL 8.x，建议验收目标为 8.4。连接后检查握手版本，其他主版本及 MariaDB 默认拒绝；设置查询时限失败或回读不一致也拒绝继续查询。尚未取得任何真实服务版本的运行证据。
- 驱动读写超时参数自 Connector/Python 9.2 起提供；当前依赖最低 9.7。当前安装驱动的参数定义和 Decimal 参数转换已离线核对。
- 纯 Python 连接显式传入 `converter_class=FixedPointMySQLConverter`，覆盖 Decimal 转换；不要切换为 C 扩展或覆盖转换器参数后仍声称通过当前验收。无需新增依赖。
- 代码仅从进程环境读取数据库配置，不自动读取 `.env`。部署者可以通过进程管理器注入环境，或主动执行 `uv run --env-file .env python main.py`。不在命令行参数中填写密码，不将初始化管理员凭据注入 Agent。

管理员账号只用于经用户确认的初始化和授权。应用账号必须为独立、最小权限的只读账号；禁止 root 默认回退。代码拒绝已知管理员名称不等于验证了该账号的实际权限，后续仍必须检查真实授权和角色。

## 会话 SQL Mode 限制

当前连接的 `@@SESSION.sql_mode` 必须恰好包含以下六项：

```text
ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION
```

比较时忽略顺序、大小写和项首尾空白；缺项、多项或空值均拒绝，特别是不接受 ANSI_QUOTES、NO_BACKSLASH_ESCAPES、PIPES_AS_CONCAT。工具只读取验证，不自动 SET SESSION sql_mode，也不修改全局配置。不兼容时会关闭连接，在执行业务 SQL 前返回明确错误。实际安装可能定制模式，须在独立 MySQL 8.4 集成环境中先核对，不要为了测试修改已有业务实例的全局模式。

SQL 输入中的双引号及反斜杠采用保守拒绝策略（包括字符串或注释内的这些字符）；标准单引号翻倍转义仍支持。该策略不限制数据库查询结果中的字符，输出继续由 CSV 转义。

## 后续管理员配置模板（本次未执行）

先确认目标实例、已有库表与数据、备份及账号是否已存在。`sql/init.sql` 保持不变，只在明确批准的新空测试库执行一次；不要向已有业务库重跑，不删除、重置、覆盖或自动补写已有数据。

下面仅是新建专用账号的模板。账号 host 必须匹配真实连接来源；不要直接替换已有同名账号，密码由管理员在本机安全设置。

```sql
CREATE USER 'tanyuan_reader'@'localhost'
  IDENTIFIED BY '<管理员本机设置的独立密码>'
  WITH MAX_USER_CONNECTIONS 3;
GRANT SELECT ON pharma_db.drugs TO 'tanyuan_reader'@'localhost';
GRANT SELECT ON pharma_db.inventory TO 'tanyuan_reader'@'localhost';
GRANT SELECT ON pharma_db.sales_records TO 'tanyuan_reader'@'localhost';
```

不授予全局或整个数据库的 ALL 权限，不授予写入、DDL、FILE、EXECUTE、CREATE TEMPORARY TABLES、管理、代理或 GRANT OPTION 权限；不绑定带有额外权限的角色。应用连接自身设置只读事务模式，是补充保护，不能代替上述授权。

权限验收由管理员和应用身份分别检查：

```sql
SELECT CURRENT_USER(), CURRENT_ROLE(), VERSION();
SHOW GRANTS;
```

管理员需进一步检查该账号被授予的角色、角色继承和代理授权，确认唯一业务权限为三表 SELECT，且三表确实为预期基础表。不得仅看用户名或 `@@transaction_read_only` 就认定权限合格。应用内受控的表列表查询通过 information_schema 可见性获取元数据，不需要额外授予整个 information_schema 的权限。

## 配置及资源限制

所需配置见 `.env.example`；账号、密码、主机和数据库缺失时，在连接前失败，不回退到 root 或空密码。

| 配置 | 默认 | 硬上限 |
| --- | --- | --- |
| MYSQL_CONNECT_TIMEOUT | 5 秒 | 30 秒 |
| MYSQL_READ_TIMEOUT / MYSQL_WRITE_TIMEOUT | 15 秒 | 60 秒 |
| MYSQL_QUERY_TIMEOUT_MS | 10000 毫秒 | 30000 毫秒 |
| MYSQL_MAX_ROWS | 200 行 | 1000 行 |
| MYSQL_MAX_RESULT_BYTES | 1048576 字节 | 4194304 字节 |

值须为正整数，超限直接报配置错误。只允许 utf8mb4。禁止 LOCAL INFILE 和驱动多语句能力；每次使用独立短连接和非缓冲游标。

连接后执行固定的参数化 `SET SESSION max_execution_time`，回读确认，并设置 `SET SESSION TRANSACTION READ ONLY`。这些是程序内部配置语句，用户 SQL 不能传入 SET。`max_execution_time` 限制只读 SELECT 的服务器执行时间；网络读写超时是独立限制，并非整个工具调用的全局总时限。

SELECT 缺少 LIMIT 时自动添加；超过行上限的 LIMIT 被收紧，较小 LIMIT 保留，预览最多 100 行。逐行读取，不使用 fetchall；超过字节上限时返回明确错误，不交付不完整 CSV。CSV 对逗号、引号和换行做标准转义；普通数据保持 TDD 的字段行和数据行格式，NULL 保持既有字符串表示 None。

字节上限限制交付文本和累计 CSV 缓冲，不等于数据库驱动底层报文的严格峰值内存上限；单行仍需要由驱动接收。超大字段在额外 CSV 序列化前被拒绝，部署时仍应合理配置服务器包大小及业务字段规模。

## SQL 明确允许规则

- 单条 SELECT；允许安全常量 SELECT，以及配置库中 drugs、inventory、sales_records 的读取。
- WHERE、比较、布尔表达式、IN 常量列表、BETWEEN、LIKE、NULL、基本算术。
- 带 ON 的 INNER、LEFT、RIGHT JOIN，GROUP BY、HAVING、ORDER BY、DISTINCT。
- COUNT、SUM、AVG、MIN、MAX、COALESCE、ROUND；其他函数默认拒绝。
- 非负整数字面量 LIMIT/OFFSET，OFFSET 不超过 100000。
- 数值仅接受整数和安全的精确小数（例如 `1`、`1.0`、`.125`、`-0.50`）。科学计数法不支持；NUMBER Token 中的 `1.`、`-1.` 等尾随小数点形式在连接前拒绝，避免 Decimal 及驱动转换抹去小数点。单引号字符串 `'1.'` 和普通注释中的 `1.` 不属于数值，不被此规则拒绝。
- Decimal 参数只支持有限值、小数位 1–30、精度不超过 65 位；超出范围默认在连接前拒绝，不舍入或截断。整数仍为 int。无小数位/正指数的 Decimal 直接参数不在支持范围内，不能隐式作为整数发送；正常 SQL 整数不受影响。
- SQL 最多 16 KiB、AST 最多 1000 个节点、单个常量最多 4096 字符。

拒绝全部非 SELECT、DDL/DCL、REPLACE、CALL、SET、多语句、跨库或其他表、CTE、所有子查询、UNION、窗口、变量、锁定、无 ON/CROSS/NATURAL JOIN、导出、索引/优化器 hint、未知节点或选项、未知或副作用函数、用户占位符、解析失败的语法。

还拒绝显式 NULLS FIRST/LAST 扩展，不模拟展开排序表达式；普通 `/` 仅允许已验证的 MySQL AST 属性，不插入 CAST、NULLIF 或重复操作数，整数 DIV 等未允许运算继续拒绝。最终生成 SQL 的词法占位符检查仅作为失败防线，不用于替换 SQL、忽略参数或放宽允许规则。

只执行验证后的 AST 重新生成的 MySQL SQL；普通注释被移除，原始 SQL 不直接执行。字符串与数值常量按生成顺序绑定为驱动参数，包括 LIMIT/OFFSET。表标识符采用严格白名单与 fullmatch，程序元数据查询的 schema 值使用参数绑定。

## Decimal 精确绑定策略

当前驱动默认用 `str(Decimal)`，可能把精确输入 `0.0000001` 输出为 `1E-7`，改变 MySQL 数值分类。连接级子类仅覆盖 `_decimal_to_mysql`，先检查有限性、精度和小数位，再用 `format(value, "f")` 输出 ASCII 固定小数文本。极大指数在格式化前拦截，避免无界文本展开；没有调整全局 Decimal 上下文或修改第三方类。

原始参数仍为 Decimal，由驱动执行转换、转义及占位符绑定；当前纯 Python 驱动的 `_process_params` 根据原始 Decimal 类型跳过字符串引号，数值不会变成 SQL 字符串。`_bound_sql` 在连接前调用同一校验器检查边界，随后仍将 Decimal 对象交给驱动，不把不可信 SQL 手工拼接起来。

已通过真实驱动离线配置与参数处理路径确认：`SELECT 0.0000001` 保持固定小数文本，`ROUND(0.00000025,7)` 不变为指数形式，负数、精确零和小数尾零得以保留。客户端转换通过不等于 MySQL 服务端计算通过。

驱动异常原文不向调用者传播，避免泄露主机、账号或凭据；保留 `连接数据库失败：...` 前缀，配置错误及超时给出稳定提示。成功、执行失败、读取失败、超时和游标创建失败都进入清理路径；游标清理失败仍继续关闭连接，清理异常不覆盖原结果。

## 离线验收

```sh
uv run python -B scripts/test_mysql_tools.py
uv run python scripts/check_stage0.py
uv pip check
```

数据库测试使用 Fake Connection/Cursor，禁真实 socket.connect 与 .env 读取。包括三个工具、SELECT/JOIN/聚合、参数顺序和转义、非法语句在连接/execute 前拒绝、配置缺失、已知管理员名、驱动参数、版本拒绝、时限回读失败、连接异常、查询/读写超时、行数/字节限制和资源释放。

离线测试结果记录见 `阶段1-离线验收.md`。Fake 结果不证明真实数据库权限、SQL 执行或超时已通过。

第四轮定向修复后的离线结果：数据库专项 32/32（原有 27 项及新增 5 项），阶段 0 回归 8/8，依赖检查和 diff 格式检查通过；各命令退出码为 0。真实 MySQL 兼容性仍待后续集成验收。

## 尚需另行授权的真实集成验收

1. 确认 MySQL 8.4 实例和只读身份、三张基础表、utf8mb4、结构和种子数量（新空库为 10/30/20）。
2. 由实际账号核验 CURRENT_USER、SHOW GRANTS、角色与代理授权；不输出密码。
3. 验证三工具真实返回、中文/引号/换行、空结果、缺表和断连行为。
4. 验证服务器时限设置和回读、实际超时中止、网络超时，以及关闭后服务端连接回收。使用经批准的隔离测试数据构造慢查询，不开放危险函数给用户工具。
5. 确认驱动常量绑定、LIMIT/OFFSET、元数据可见性、只读事务设置与目标服务端兼容。
6. 如需验证数据库层拒写，只在另行授权的隔离测试库进行；不向已有业务数据试写。
7. 验证真实纯 Python 连接使用 FixedPointMySQLConverter，并核对极小数、ROUND、高精度零及精度 65/小数位 30 边界的实际结果、类型和警告；连接器或执行模式升级后重跑绑定测试。表达式计算结果的范围和精度仍由 MySQL 规则决定，本工具不保证任意算术表达式不溢出或不发生服务端舍入。

参考：[MySQL max_execution_time](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time)、[Connector/Python 参数](https://dev.mysql.com/doc/connector-python/en/connector-python-connectargs.html)、[SQLGlot AST 文档](https://sqlglot.com/sqlglot.html)。

## 2026-10-27 后续真实验收

阶段 1 真实 MySQL 8.4.13 验收已完成：6/6 PASS（专用三表 SELECT 账号）；32 项离线与 8 项阶段 0 回归继续通过。详见 `阶段1-真实集成验收.md`。此前“尚未执行”描述保留作为对应离线修复时的历史记录。
