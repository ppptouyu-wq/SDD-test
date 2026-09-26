"""Brownfield 工具：按第 7.4 节四步法为存量模块补写规范。

书中的四步法（印刷 p.141）：
  第一步 逆向工程 —— 用 AI 辅助分析代码，提取对外接口、外部依赖、异常处理、行为边界
  第二步 规范化   —— 整理为 design.md 级别的接口契约；**必须人工介入**，
                    因为"现有行为"不等于"正确行为"
  第三步 补写测试 —— 依据接口契约生成测试，用于暴露未覆盖的边界
  第四步 锁定基线 —— 测试全绿后连同规范一起提交；此后该模块"先更新规范，再改代码"

本模块实现第一步与第二步的**自动化部分**，并把第三步的"可疑行为"清单交给人工判断。

设计原则：分析器只做**客观提取**，不做判断。判断（哪些是 bug、哪些是特性）留给
第二步的人类审查 —— 这正是书中强调"必须由人工介入"的原因。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

# 魔法数字：这些字面量出现在代码里但没有任何说明（书中称为"隐性决策"）。
# 注意：这里**不猜测**每个数字的含义 —— 猜错会误导人工审查。
# 只标注"该数字需要被解释"，由第二步的人类判断它究竟代表什么。
_SUSPICIOUS_NUMBERS = {
    3, 5, 30, 60, 100, 200, 403, 429, 465, 1536, 3072,
}
_SKIP_NUMBERS = {0, 1, 2}  # 太常见，不算可疑


@dataclass
class FunctionInfo:
    """一个函数的客观信息。"""

    name: str
    args: list[str]
    docstring: str | None
    line: int
    has_type_hints: bool
    magic_numbers: list[tuple[int, int, str]] = field(default_factory=list)  # (值, 行号, 用途)
    raises: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    """一个类的客观信息。"""

    name: str
    line: int
    methods: list[FunctionInfo] = field(default_factory=list)


@dataclass
class ModuleAnalysis:
    """一个模块的逆向工程结果。"""

    path: str
    module_docstring: str | None
    imports: list[str] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    undocumented: list[str] = field(default_factory=list)
    implicit_decisions: list[str] = field(default_factory=list)
    boundary_flags: list[str] = field(default_factory=list)

    @property
    def public_functions(self) -> list[str]:
        names = [f.name for f in self.functions if not f.name.startswith("_")]
        for klass in self.classes:
            names.extend(
                f"{klass.name}.{m.name}" for m in klass.methods if not m.name.startswith("_")
            )
        return names


# 最近一次 analyze_tree 中被跳过的文件：[(路径, 原因)]
skipped: list[tuple[str, str]] = []


def _collect_magic_numbers(node: ast.AST, source_lines: list[str]) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, int):
            if sub.value in _SKIP_NUMBERS or sub.value not in _SUSPICIOUS_NUMBERS:
                continue
            line_text = (
                source_lines[sub.lineno - 1].strip() if 0 < sub.lineno <= len(source_lines) else ""
            )
            found.append((sub.value, sub.lineno, line_text[:70]))
    return found


def _collect_raises(node: ast.AST) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Raise) and sub.exc is not None:
            exc = sub.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                names.append(exc.id)
            elif isinstance(exc, ast.Attribute):
                names.append(exc.attr)
    return sorted(set(names))


def _collect_calls(node: ast.AST) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return sorted(set(names))


def _has_hints(node: ast.FunctionDef) -> bool:
    if node.returns is not None:
        return True
    return all(arg.annotation is not None for arg in node.args.args if arg.arg != "self")


def analyze_module(path: str | Path) -> ModuleAnalysis:
    """对单个模块做客观逆向工程。

    只提取事实（接口、依赖、异常、魔法数字、边界分支），不做价值判断。

    :raises SyntaxError: 文件无法解析（调用方可跳过）
    :raises UnicodeDecodeError: 文件编码异常（如带 BOM）
    """
    module_path = Path(path)
    source = module_path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source)

    analysis = ModuleAnalysis(
        path=str(module_path),
        module_docstring=ast.get_docstring(tree),
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            analysis.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                analysis.imports.append(node.module)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            info = _function_info(node, source_lines)
            analysis.functions.append(info)
            if info.docstring is None:
                analysis.undocumented.append(info.name)
        elif isinstance(node, ast.ClassDef):
            klass = ClassInfo(
                name=node.name,
                line=node.lineno,
                methods=[
                    _function_info(sub, source_lines)
                    for sub in node.body
                    if isinstance(sub, ast.FunctionDef)
                ],
            )
            analysis.classes.append(klass)
            for method in klass.methods:
                if method.docstring is None:
                    analysis.undocumented.append(f"{klass.name}.{method.name}")

    analysis.implicit_decisions = _implicit_decisions(analysis, source)
    analysis.boundary_flags = _boundary_flags(source)
    return analysis


def _function_info(node: ast.FunctionDef, source_lines: list[str]) -> FunctionInfo:
    return FunctionInfo(
        name=node.name,
        args=[a.arg for a in node.args.args],
        docstring=ast.get_docstring(node),
        line=node.lineno,
        has_type_hints=_has_hints(node),
        magic_numbers=_collect_magic_numbers(node, source_lines),
        raises=_collect_raises(node),
        calls=_collect_calls(node),
    )


def _implicit_decisions(analysis: ModuleAnalysis, source: str) -> list[str]:
    """识别"只在代码里、从未被记录"的决策（书中称为隐性知识）。"""
    decisions: list[str] = []

    for func in analysis.functions + [m for c in analysis.classes for m in c.methods]:
        for value, lineno, line_text in func.magic_numbers:
            decisions.append(
                f"{func.name}() 第 {lineno} 行出现字面量 {value}，"
                f"源码为 `{line_text}` —— 用途与取值理由无任何说明，需人工确认"
            )
        if func.raises:
            decisions.append(
                f"{func.name}() 会抛出 {', '.join(func.raises)} —— 未在任何文档中约定"
            )
        if not func.has_type_hints:
            decisions.append(f"{func.name}() 缺少类型标注 —— 参数与返回类型只能靠推断")

    # 排序等隐式行为
    if re.search(r"sorted\(", source):
        decisions.append("存在 sorted() 调用 —— 输出顺序是被隐式保证的，但从未写入规范")
    if re.search(r"\.get\([^)]*,\s*0\)", source):
        decisions.append("存在 dict.get(key, 0) 形式的兜底 —— 字段缺失会被静默填 0，无法区分'缺失'与'真为 0'")
    return decisions


def _boundary_flags(source: str) -> list[str]:
    """识别需要人工确认的边界行为。"""
    flags: list[str] = []
    if re.search(r"except\s+Exception", source) or re.search(r"except:\s*$", source, re.MULTILINE):
        flags.append("存在裸 except / except Exception —— 会吞掉所有异常，需确认是否为有意为之")
    if re.search(r"pass\s*$", source, re.MULTILINE):
        flags.append("存在 pass 占位 —— 可能是未完成的错误处理")
    if re.search(r"time\.sleep\(", source):
        flags.append("存在 time.sleep —— 阻塞式等待，需确认是否会拖垮调用方")
    if "return True" in source and "except" in source:
        flags.append("异常路径中返回 True —— 需确认失败是否被掩盖")
    return flags


def analyze_tree(root: str | Path) -> list[ModuleAnalysis]:
    """递归分析一个目录下的所有 .py 模块。

    无法解析的文件（语法错误、编码问题如带 BOM）会被**跳过并记录**，
    而不是让整个分析中断 —— 存量系统里本来就存在这类文件，
    分析工具必须能容忍它们（否则第一步就卡住，四步法根本走不下去）。
    """
    base = Path(root)
    results: list[ModuleAnalysis] = []
    skipped.clear()
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            results.append(analyze_module(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            skipped.append((str(path), f"{type(exc).__name__}: {exc}"))
    return results


def render_interface_contract(analysis: ModuleAnalysis, *, module_name: str) -> str:
    """第二步的产物：把逆向工程结果整理成 design.md 级别的接口契约草稿。

    ⚠️ 这是**草稿**。书中 7.4.3 强调第二步"必须由人工介入进行判断，
    因为代码中的'现有行为'并不等同于'正确行为'"。本函数只负责把事实排好版，
    并在每处需要判断的地方留下 TODO 供人确认。
    """
    lines = [
        f"## {module_name}",
        "",
        f"> 逆向工程来源：`{analysis.path}`",
        "> 本契约由工具生成草稿，**必须经人工审查后才能定稿**（第 7.4.3 节第二步）。",
        "",
        "### 对外接口",
        "",
        "| 函数 | 参数 | 返回 | 异常 |",
        "|---|---|---|---|",
    ]
    for func in analysis.functions:
        if func.name.startswith("_"):
            continue
        params = ", ".join(func.args) or "—"
        raises = ", ".join(func.raises) or "—"
        lines.append(f"| `{func.name}` | {params} | 待确认 | {raises} |")
    for klass in analysis.classes:
        for method in klass.methods:
            if method.name.startswith("_"):
                continue
            params = ", ".join(method.args) or "—"
            raises = ", ".join(method.raises) or "—"
            lines.append(f"| `{klass.name}.{method.name}` | {params} | 待确认 | {raises} |")

    lines += ["", "### 外部依赖", ""]
    for dep in sorted(set(analysis.imports)):
        lines.append(f"- `{dep}`")

    lines += [
        "",
        "### 需要人工判断的隐性决策",
        "",
        "> 以下每一条都对应代码中的「现有行为」。**现有行为不等于正确行为** ——",
        "> 第二步的人工审查必须逐条判断：保留、修正，还是标记为历史遗留的临时方案。",
        "",
    ]
    for i, decision in enumerate(analysis.implicit_decisions, 1):
        lines.append(f"{i}. {decision}")
        lines.append("   - [ ] 人工判断：保留 / 修正 / 标记为遗留  —— 理由：")

    if analysis.boundary_flags:
        lines += ["", "### 边界行为待确认", ""]
        for flag in analysis.boundary_flags:
            lines.append(f"- {flag}")

    return "\n".join(lines)
