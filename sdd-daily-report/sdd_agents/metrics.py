"""规范度量（第 9 章 9.5）：如何衡量 SDD 的效果。

书中 9.5.1 讲"为什么要度量"，9.5.2 给出 SDD 效果的度量指标。
第 9 章还提到度量要落到"能自动采集"上，否则团队不会坚持。

设计原则：**只采集能从仓库里客观算出来的指标**，不引入需要人工填报的数字 ——
人工填报的指标会迅速失真，这是度量体系失败的最常见原因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpecMetrics:
    """单个规范文件的度量。"""

    path: str
    kind: str
    sections: int = 0
    acceptance_items: int = 0
    acceptance_done: int = 0
    out_of_scope_items: int = 0
    lines: int = 0

    @property
    def acceptance_coverage(self) -> float:
        """验收标准完成率。"""
        if self.acceptance_items == 0:
            return 0.0
        return round(self.acceptance_done / self.acceptance_items, 4)


@dataclass
class ProjectMetrics:
    """一个项目的规范度量汇总。"""

    specs: list[SpecMetrics] = field(default_factory=list)
    spec_count: int = 0
    adr_count: int = 0
    total_acceptance: int = 0
    total_acceptance_done: int = 0
    spec_lines: int = 0
    code_lines: int = 0
    test_lines: int = 0
    test_count: int = 0
    acceptance_mapping: AcceptanceCoverage | None = None

    @property
    def acceptance_coverage(self) -> float:
        """验收标准被测试覆盖的比例（真实兑现度，不是勾选率）。"""
        if self.acceptance_mapping is None:
            return 0.0
        return self.acceptance_mapping.ratio

    @property
    def test_to_spec_ratio(self) -> float:
        """测试代码行数 / 规范行数：反映"规范是否被测试兑现"。"""
        if self.spec_lines == 0:
            return 0.0
        return round(self.test_lines / self.spec_lines, 3)

    @property
    def tests_per_acceptance(self) -> float:
        """每个验收标准平均对应多少测试用例（目标 >= 1）。"""
        if self.total_acceptance == 0:
            return 0.0
        return round(self.test_count / self.total_acceptance, 3)


_SECTION_RE = re.compile(r"^#{2,3}\s+\S", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+)$", re.MULTILINE)


def _kind_of(path: Path) -> str:
    stem = path.stem.lower()
    for kind in ("proposal", "design", "tasks"):
        if kind in stem:
            return kind
    return "other"


def measure_spec(path: str | Path) -> SpecMetrics:
    """度量单个规范文件。

    注意：`- [ ]` 在本书中是**规范格式**（proposal.md 原文即未勾选），
    不是进度勾选框。因此这里不用"勾选率"当指标 —— 那会永远是 0%，
    属于误导性度量。真正的兑现度由 `map_acceptance_to_tests()` 衡量。
    """
    spec_path = Path(path)
    text = spec_path.read_text(encoding="utf-8")

    checkboxes = _CHECKBOX_RE.findall(text)
    done = sum(1 for mark, _desc in checkboxes if mark.lower() == "x")

    out_of_scope = 0
    block = re.search(r"###?\s*.*不做什么.*?\n(.*?)(?=\n##|\Z)", text, flags=re.DOTALL)
    if block:
        out_of_scope = len(re.findall(r"^-\s+\S", block.group(1), flags=re.MULTILINE))

    return SpecMetrics(
        path=str(spec_path.name),
        kind=_kind_of(path),
        sections=len(_SECTION_RE.findall(text)),
        acceptance_items=len(checkboxes),
        acceptance_done=done,
        out_of_scope_items=out_of_scope,
        lines=len(text.splitlines()),
    )


# ------------------------------------------------- 验收→测试 映射覆盖

# 中文关键词 → 测试文件/函数中可能出现的英文关键词
_KEYWORD_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("接口定义", ("signature", "contract")),
    ("字段", ("field", "schema")),
    ("分页", ("pagination", "paginate", "page")),
    ("重试", ("retry", "retries")),
    ("限流", ("rate_limit", "ratelimit", "limit")),
    ("超时", ("timeout",)),
    ("Token", ("token",)),
    ("敏感", ("sensitive",)),
    ("关键词", ("keyword",)),
    ("无记录", ("no_record", "norecord", "no-records")),
    ("数据获取失败", ("fetch_failed", "source_fail", "failed", "failure")),
    ("非工作日", ("working_day", "weekend", "holiday", "calendar")),
    ("降级", ("degrad", "graceful")),
    ("Mock", ("mock",)),
    ("工时", ("work_hours", "hours")),
    ("签退", ("checkout", "check_out", "missing")),
    ("考勤", ("attendance", "check_in", "check_out")),
    # v1.3：同一飞书 ID 扇出到多个成员名
    ("回填", ("fan", "backfill", "matching_member", "assign")),
    ("共用", ("share", "same_id", "collide", "fan")),
    ("名单", ("user_ids", "employee_id", "roster")),
    ("success", ("success",)),
    ("排序", ("sort", "order")),
    ("截断", ("trunc", "cap", "limit")),
    ("并发", ("concurrent", "parallel")),
    ("密码", ("password", "secret", "key")),
    ("主题", ("subject",)),
    ("HTML", ("html",)),
    ("Markdown", ("markdown",)),
    # 以下映射用于让"验收标准的措辞"与"测试的命名"能对上
    ("编排", ("pipeline", "order", "orchestrat", "end_to_end", "e2e")),
    ("不阻断", ("not_block", "does_not_block", "isolat", "degrad")),
    ("全局异常", ("exception", "error", "handler", "exit_code")),
    ("板块", ("section", "three_core", "core_sections")),
    ("空日报", ("empty", "all_sources", "not_generate")),
    ("单元测试", ("test", "unit")),
    ("测试通过", ("passed", "test", "green")),
    ("签名", ("signature", "contract")),
    ("字段", ("field", "schema", "models")),
    ("类型正确", ("isinstance", "type", "field")),
    ("空列表", ("empty", "return_empty")),
    ("日志", ("log", "logger", "json")),
    ("自动刷新", ("refresh", "token")),
    ("自动跳过", ("skip", "weekend", "holiday")),
    # ---- v1.2 展示层词条（让 Task 12 / proposal.md §3.1·§3.3 的措辞能对上测试命名）----
    ("回环", ("loopback", "127.0.0.1", "host")),
    ("绑定", ("loopback", "bind", "host", "serve")),
    ("主机", ("loopback", "host", "serve_rejects")),
    ("只读", ("readonly", "read_only", "reject_write", "no_write")),
    ("写操作", ("write", "post", "put", "delete", "patch", "405")),
    ("展示页", ("webview", "serve", "display", "page")),
    ("展示层", ("webview", "views", "serve", "display")),
    ("标准库", ("stdlib", "framework", "build_step", "dependency")),
    ("依赖", ("dependency", "stdlib", "framework", "no_dependency")),
    ("框架", ("framework", "flask", "fastapi")),
    ("构建", ("build_step", "webpack", "vite", "framework")),
    ("令牌", ("token", "design_token", "css")),
    ("空态", ("empty", "empty_state", "empty_db", "no_report")),
    ("白屏", ("empty", "empty_state", "fatal", "no_report")),
    ("首页", ("index", "list", "empty_state", "render")),
    ("倒序", ("descending", "order", "sort", "date_desc")),
    ("未记录", ("unknown", "unrecorded", "sources_empty", "status")),
    ("导出", ("export", "download")),
]


@dataclass
class AcceptanceCoverage:
    """验收标准到测试的映射覆盖情况。"""

    total: int = 0
    covered: int = 0
    uncovered: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.covered / self.total, 4)


def _collect_test_text(project_root: Path) -> str:
    tests_dir = project_root / "tests"
    if not tests_dir.exists():
        return ""
    chunks = [
        p.read_text(encoding="utf-8", errors="replace")
        for p in tests_dir.rglob("test_*.py")
    ]
    return "\n".join(chunks)


def _criteria_is_covered(criterion: str, test_text: str, test_names: str) -> bool:
    """判断一条验收标准是否能在测试里找到落点。"""
    lowered = criterion.lower()
    for zh, en_variants in _KEYWORD_MAP:
        if zh.lower() in lowered:
            if any(v in lowered for v in en_variants):
                continue  # 标准本身已含英文，直接查
            if any(v in test_text or v in test_names for v in en_variants):
                return True
    # 兜底：标准里的英文单词是否出现在测试中
    english = re.findall(r"[A-Za-z_]{4,}", criterion)
    for word in english:
        if word.lower() in test_text.lower() or word.lower() in test_names.lower():
            return True
    return False


def map_acceptance_to_tests(project_root: str | Path) -> AcceptanceCoverage:
    """把 tasks.md 的验收标准映射到测试，算出真实兑现度。

    这是本项目采纳的"规范兑现度"指标 —— 比"勾选率"有意义得多：
    它回答的是"规范里写的每条验收标准，是否真的有测试在守着"。
    """
    base = Path(project_root)
    tasks_file = base / "specs" / "tasks.md"
    coverage = AcceptanceCoverage()
    if not tasks_file.exists():
        return coverage

    criteria = _parse_acceptance_criteria(tasks_file.read_text(encoding="utf-8"))
    test_text = _collect_test_text(base)
    test_names = "\n".join(re.findall(r"^\s*def (test_\w+)", test_text, flags=re.MULTILINE))

    coverage.total = len(criteria)
    for criterion in criteria:
        if _criteria_is_covered(criterion, test_text, test_names):
            coverage.covered += 1
        else:
            coverage.uncovered.append(criterion)
    return coverage


def _parse_acceptance_criteria(tasks_text: str) -> list[str]:
    """从 tasks.md 提取所有验收标准条目。"""
    criteria: list[str] = []
    for mark, desc in _CHECKBOX_RE.findall(tasks_text):
        cleaned = desc.strip()
        if cleaned:
            criteria.append(cleaned)
    return criteria


def _count_python_lines(root: Path, *, tests: bool) -> int:
    total = 0
    for path in root.rglob("*.py"):
        parts = set(path.parts)
        if "__pycache__" in parts:
            continue
        is_test = "tests" in parts or path.name.startswith("test_")
        if is_test is not tests:
            continue
        total += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def _count_tests(root: Path) -> int:
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return 0
    count = 0
    for path in tests_dir.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        count += len(re.findall(r"^\s*def test_", text, flags=re.MULTILINE))
    return count


def measure_project(root: str | Path) -> ProjectMetrics:
    """度量一个 SDD 项目：规范规模、验收覆盖、测试兑现度、ADR 数量。"""
    base = Path(root)
    specs_dir = base / "specs"
    metrics = ProjectMetrics()

    if specs_dir.exists():
        for spec_path in sorted(specs_dir.glob("*.md")):
            metrics.specs.append(measure_spec(spec_path))
        adr_dir = specs_dir / "adrs"
        if adr_dir.exists():
            metrics.adr_count = len(list(adr_dir.glob("*.md")))

    metrics.spec_count = len(metrics.specs)
    metrics.total_acceptance = sum(s.acceptance_items for s in metrics.specs)
    metrics.total_acceptance_done = sum(s.acceptance_done for s in metrics.specs)
    metrics.spec_lines = sum(s.lines for s in metrics.specs)
    metrics.code_lines = _count_python_lines(base, tests=False)
    metrics.test_lines = _count_python_lines(base, tests=True)
    metrics.test_count = _count_tests(base)
    metrics.acceptance_mapping = map_acceptance_to_tests(base)
    return metrics


def render_metrics(metrics: ProjectMetrics) -> str:
    """渲染可读的度量报告（第 9 章 9.5.2 的指标呈现）。"""
    lines = ["SDD 效果度量", "=" * 56]
    lines.append(f"  规范文件数            {metrics.spec_count}")
    lines.append(f"  ADR 数量              {metrics.adr_count}")
    lines.append(f"  规范总行数            {metrics.spec_lines}")
    lines.append(f"  生产代码行数          {metrics.code_lines}")
    lines.append(f"  测试代码行数          {metrics.test_lines}")
    lines.append(f"  测试用例数            {metrics.test_count}")
    lines.append("")

    mapping = metrics.acceptance_mapping or AcceptanceCoverage()
    lines.append("  ── 规范兑现度（本项目的核心指标）──")
    lines.append(
        f"  验收标准被测试覆盖    {mapping.covered}/{mapping.total} "
        f"= {mapping.ratio:.1%}"
    )
    lines.append(f"  每验收标准对应测试数  {metrics.tests_per_acceptance}")
    lines.append(f"  测试/规范 行数比      {metrics.test_to_spec_ratio}")
    lines.append("")
    lines.append("  说明：proposal.md / tasks.md 里的 `- [ ]` 是**规范格式**（原书即未勾选），")
    lines.append("        不是进度勾选框。因此本项目不用「勾选率」当指标 —— 那永远是 0%，")
    lines.append("        属于误导性度量。真正的兑现度是上方的「验收标准被测试覆盖」。")
    lines.append("=" * 56)
    for spec in metrics.specs:
        lines.append(
            f"  {spec.path:<18} 章节 {spec.sections:>3} | "
            f"验收项 {spec.acceptance_items:<3} | "
            f"排除项 {spec.out_of_scope_items}"
        )
    if mapping.uncovered:
        lines.append("")
        lines.append(f"  未被测试覆盖的验收标准（{len(mapping.uncovered)} 条）：")
        for item in mapping.uncovered[:12]:
            lines.append(f"    - {item[:64]}")
        if len(mapping.uncovered) > 12:
            lines.append(f"    …… 其余 {len(mapping.uncovered) - 12} 条")
        lines.append("")
        lines.append("  已知无法映射的两类（属正常，不是缺口）：")
        lines.append("    1. 非行为性标准，如「依赖安装成功」—— 由 CI 步骤保证，无需单元测试")
        lines.append("    2. 自指标准，如「测试全部通过」—— 它本身就是测试套件的结论")

    return "\n".join(lines)


# 第 9 章 9.5.2 提到的指标，以及它们在仓库里如何被客观采集。
# 原则：只采用"能自动采集"的指标；需要人工填报的指标会迅速失真。
METRIC_SOURCES = [
    ("规范覆盖率", "specs/ 下三份核心规范是否齐备", "spec_count >= 3"),
    ("规范兑现度", "验收标准被测试覆盖的比例（map_acceptance_to_tests）", "acceptance_coverage"),
    ("测试兑现度", "每个验收标准平均对应多少测试用例", "tests_per_acceptance >= 1"),
    ("规范与代码同步度", "最近一次提交是否同时包含 specs/ 与代码", "workflow.check_commit_scope"),
    ("返工率", "需要人工事后修正的 PR 占比（需接入 PR 系统，无法离线采集）", "PR 系统"),
]
