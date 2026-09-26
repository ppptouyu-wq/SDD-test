"""入护栏：在 AI 修改文件之前拦截越界行为（护栏三明治模式，第 8 章 8.6.3）。

书中的三条规则（图 8-13 input_guard）：
1. 禁止修改 specs/ 下的规范文件（规范变更必须由人类确认）
2. 禁止实现 proposal.md § 2.2 已明确排除的范围
3. 禁止硬编码密钥（design.md § 6.2 安全约束）

用法（在 Claude Code Hooks 的 PreToolUse 中调用）：
    工具调用信息从 stdin 传入，本脚本以退出码表达判定：
    0 = 放行，2 = 拦截
"""

from __future__ import annotations

import json
import re
import sys

# ---- 规则 1：受保护的规范目录 ----
PROTECTED_PATHS = ("specs/", "specs\\")

# ---- 规则 2：proposal.md §2.2 明确排除的范围 ----
# 命中这些词说明 AI 在实现"本期不做"的功能
FORBIDDEN_SCOPE_PATTERNS = [
    (r"llm[_\-]?summar|智能摘要|ai[_\-]?总结|gpt[_\-]?summary", "proposal.md §2.2 排除：不做智能摘要和 AI 总结"),
    (r"mobile[_\-]?app|移动端|android|ios[_\-]?client", "proposal.md §2.2 排除：不做移动端"),
    (r"multi[_\-]?tenant|多租户", "proposal.md §2.2 排除：不做多租户架构"),
    (r"history[_\-]?chart|统计图表|报表分析", "proposal.md §2.2 排除：不做历史日报的统计图表分析"),
]

# ---- 规则 3：硬编码密钥 ----
SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{12,}[\"']", "疑似硬编码密钥"),
    (r"ghp_[A-Za-z0-9]{20,}", "疑似硬编码 GitHub Token"),
    (r"sk-[A-Za-z0-9]{20,}", "疑似硬编码 OpenAI 风格密钥"),
]


def check(payload: dict) -> list[str]:
    """返回违规原因列表；空列表表示放行。"""
    violations: list[str] = []

    tool_input = payload.get("tool_input") or {}
    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    content = str(tool_input.get("content") or tool_input.get("new_string") or "")

    # 规则 1：保护 specs/
    if file_path and any(part in file_path for part in PROTECTED_PATHS):
        violations.append(
            f"禁止直接修改规范文件（{file_path}）。规范变更需先更新 tasks.md 并由人类确认。"
        )

    # 规则 2：越界功能
    for pattern, reason in FORBIDDEN_SCOPE_PATTERNS:
        if re.search(pattern, content, flags=re.IGNORECASE):
            violations.append(f"越界实现：{reason}")

    # 规则 3：硬编码密钥
    for pattern, reason in SECRET_PATTERNS:
        if re.search(pattern, content):
            violations.append(f"安全违规：{reason}（请改用环境变量注入）")

    return violations


def main() -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    violations = check(payload)
    if violations:
        print("入护栏拦截：", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
