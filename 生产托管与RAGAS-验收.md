# FastAPI 前端托管与 RAGAS 生成评估

2026-10-30，America/New_York。范围：补齐 TDD §3 的生产静态托管与 §9.4 的进阶评估。用户明确授权本次发送检索片段至 DeepSeek，新增最多 100 次调用；没有数据库迁移、索引重建或 Git 提交。

## 前端托管

- `api/server.py` 从项目绝对路径定位 `frontend/dist`，首页返回构建的 HTML，静态资源由 FastAPI 提供；REST / WebSocket 路由优先，未知 API 不回退到 HTML。
- 新增 `GET /api/health`，健康检查不加载 Agent、不初始化外部客户端。未构建前端时仍可单独运行后端，根路径保留原 JSON 健康响应。
- 服务继续绑定 `127.0.0.1:8000`，使用单进程。此次不涉及公网域名、TLS、反向代理或账号鉴权。
- 当前前端无需修改：fetch 与 WebSocket 均使用当前页面的同源地址。

运行方式（在项目根目录；先构建，再启动或重启）：

```sh
uv sync --locked
npm --prefix frontend run build
uv run --locked python main.py
```

访问 `http://127.0.0.1:8000/`。无需启动 Vite。业务任务凭据继续由启动进程环境注入，服务不自动加载 `.env`。

本次没有覆盖旧构建：先构建到独立目录，再把原 `frontend/dist` 保留到 `output/production-build-backup-aa53dd4a094149c596861f2e9ac02e0c/dist`，最后将新构建移入 `frontend/dist`。构建成功，1604 模块；仍有主 chunk 约 1.23 MB 的体积告警。

## RAGAS 修复

- 移除失效 `_get_embedding`，使用 `utils.knowledge_utils.get_embedding`；可选依赖写入 `pyproject.toml` 与 `uv.lock`，通过 `uv sync --locked --extra eval` 安装。
- 锁定 RAGAS 0.3 系列与兼容的 langchain-community 0.3 系列，避免 0.4 community 移除 VertexAI 模块导致 RAGAS 无法导入。不改系统 Python。
- 显式注入模型与本地 BGE Embedding，禁止评估库隐式使用默认 OpenAI 服务。RAGAS 遥测关闭，模型 SDK 无自动重试；RAGAS 运行层为单 worker、一次尝试。输出格式修复仍可能产生额外模型请求，均受同一硬额度计数。
- 每个来源分块分别传给评估器，避免把 top-k 拼成一个 context 而失真。检索失败、空答案、非法题集、缺失/NaN 评分均不能判定执行成功。
- 真实运行必须显式传 `--allow-context-upload` 与 `--budget-file`；每次实际模型请求由 `BudgetCallback` 预扣。额度文件不自动创建、重置或扩额。
- 生成阶段与 RAGAS 评分阶段使用不同的异步客户端，避免 `asyncio.run` 关闭事件循环后复用 httpx 连接；新增 `--rows /path/to/rows.json`，可在原额度下恢复评分，不重复生成答案。
- 结果保存到独立 UUID 目录：逐题答案、context、参考答案及各项分数。目录 0700、结果文件 0600，均在 Git 忽略的 output 下。失败有独立状态记录，不伪造指标。

只做无调用检查：

```sh
uv sync --locked --extra eval
uv run --locked --extra eval python -B scripts/eval_ragas.py --check
```

实际评估需在进程环境配置 DeepSeek 与本地 BGE 路径，指定已有索引与离线模式，并使用已明确授权的 0600 额度文件：

```sh
uv run --locked --extra eval python -B scripts/eval_ragas.py \
  --allow-context-upload --budget-file /absolute/path/to/authorized-budget.json
```

本次使用全部 10 条现有题集、现有私有索引、top-k=4。为控制本次 100 次额度，Answer Relevancy 的 strictness=1（每题一次反向问题生成，非默认的三次）；其他三项保留原有指标定义。本次未设生成质量通过阈值，执行成功与质量达标分开报告。题集参考答案未经独立专家复核，指标仅用于此基线。

实现参考：[RAGAS evaluate](https://docs.ragas.io/en/stable/references/evaluate/)、[RunConfig](https://docs.ragas.io/en/stable/howtos/customizations/run_config/)；当前采用锁定版本的 LangChain wrappers，未来升级需迁移兼容接口。

## 本地验收

| 检查 | 真实结果 |
| --- | --- |
| 前端生产构建 | PASS，1604 模块 |
| `scripts/test_deployment_ragas.py` | 9/9 OK；真实静态响应/WS、路由优先、路径拒绝、后端独立模式、检索失败阻断、题集校验、恢复评分使用独立客户端；真实 RAGAS 引擎配 Fake 模型与本地 Fake embedding，5 次模型调用均被预算计数，不冒充真实质量评估 |
| `scripts/check_stage0.py` | 8 项路径回归 OK；实际 localhost 子进程的 health、首页、JS/CSS 全部 HTTP 200；健康检查未加载 Agent |
| `scripts/test_api_lifecycle.py` | 10/10 OK，替代 Agent 的接口回归 |
| `scripts/test_agent_loop.py` | 17/17 OK，离线编排回归 |
| `scripts/eval_ragas.py --check` | 10 条题集及依赖检查通过，未加载模型或调用 API |
| `uv pip check` | 所有依赖兼容 |

## 真实评估证据

授权额度与日志目录：`output/ragas-authorized-7b0b588ff1d2442696c95468da369d05/`。本次独立额度上限 100，不占用或更改此前阶段额度文件。凭据仅由验收子进程读取并注入，没有输出密钥。最终指标与调用数见后续真实结果。

### 真实运行最终结果

**执行完成，退出码 0；全部 10 道题 × 4 项指标共 40 项评分均为有限值。**

| 指标 | 均值 |
| --- | --- |
| Faithfulness（忠实度） | 0.889670 |
| Answer Relevancy（答案相关性） | 0.528620 |
| Context Precision（上下文精确率） | 0.666667 |
| Context Recall（上下文召回率） | 0.600000 |

- 首次运行生成全部 10 条答案后，在第一项裁判调用发生 `OpenAIConnectionError`，退出 1，累计预扣 11 次。失败状态及原始答案保留在 `ragas-d3502a745dc5491d8f3a69f48948bb11/`。检查代码发现异步客户端跨 `asyncio.run` 的关闭事件循环复用；改为独立裁判客户端，并补恢复入口与回归覆盖。
- 恢复时读取已有 rows.json，没有重新检索或生成答案；使用同一预算文件执行 80 次评分调用后成功。最终累计 **DeepSeek 91/100**，剩余 9；没有扩额或重置计数，没有调用 Tavily。计数为本地请求前预扣，不是服务商账单证明。
- 最终逐题评分：`output/ragas-authorized-7b0b588ff1d2442696c95468da369d05/ragas-a27e296f313c4a259f77f587f3b01fc3/result.json`；对应 `rows.json` 包含完整评估输入，属私有产物，不提交。
- 恢复运行日志为授权目录的 `resume.log`，完整题集结果已保留。四道题的 Answer Relevancy 为 0，四道题的 Context Recall 为 0；整体答案相关性和覆盖不足，应作为后续优化的基线。此次没有据此调参、改题集或重建索引，也不将“脚本执行成功”写成“生成质量已达标”。
- `git diff --check` 通过。此次未暂存或提交 Git。
