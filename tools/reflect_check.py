"""反思循环硬校验器（TDD §5.2 H1~H4）。"""
from __future__ import annotations

# H1：最小报告正文字数
MIN_REPORT_LENGTH = 1000
# H2：占位符关键词
PLACEHOLDER_KEYWORDS = ["待补充", "TODO", "等待子任务", "占位", "（略）"]


def hard_check(result: dict) -> list[str]:
    """执行 H1~H4 硬校验，返回未通过项列表（空列表表示全部通过）。

    ``result`` 由编排层 ``main_agent_run`` 返回，字段：
    - ``report``           : 报告正文（str）
    - ``called_sources``   : 本轮实际调用的数据源 key 列表
    - ``required_sources`` : 主智能体规划阶段声明的数据源 key 列表
    - ``require_pdf``      : 任务是否要求 PDF（bool）
    - ``pdf_produced``     : 是否已产出 PDF 文件（bool）

    校验项：
    - H1 篇幅：报告正文字数 < 1000
    - H2 占位符：含「待补充 / TODO / 等待子任务 / 占位 /（略）」等关键词
    - H3 格式：要求 PDF 但未产出 PDF 文件
    - H4 数据源覆盖：声明要求的数据源未被实际检索
    """
    report = result.get("report", "")
    issues: list[str] = []

    # H1 篇幅
    if not report.strip():
        issues.append("回答为空")
    if result.get("report_requested", True) and len(report.strip()) < MIN_REPORT_LENGTH:
        issues.append("报告篇幅不足 1000 字")

    # H2 占位符
    if any(kw in report for kw in PLACEHOLDER_KEYWORDS):
        issues.append("报告存在未填充占位符")

    # H3 格式
    if result.get("require_pdf") and not result.get("pdf_produced"):
        issues.append("未生成要求的 PDF 文件")

    # H4 数据源覆盖
    called = set(result.get("called_sources", []))
    for src in result.get("required_sources", []):
        if src not in called:
            issues.append(f"缺少 {src} 的检索结果")

    counts = result.get("source_counts", {})
    if "web_search" in result.get("required_sources", []) and counts.get("web_search", 0) < 2:
        issues.append("网络检索未完成两个不同角度")
    if "knowledge" in result.get("required_sources", []) and counts.get("knowledge", 0) < 3:
        issues.append("知识库检索未完成三次")

    return issues
