"""演示数据：无需外部凭据即可跑通"同步 → 检索"。

用途：
  1. `create_demo_app()` 启动一个可交互的检索服务
  2. `python -m kb_search` 在终端做一次同步 + 检索演示
  3. 集成测试的语料来源
"""

from __future__ import annotations

from datetime import datetime, timezone

from kb_search.embeddings import LocalHashEmbedding
from kb_search.models import Document
from kb_search.service import IngestService, SearchService
from kb_search.sources import MockFeishuDocSource
from kb_search.vectorstore import InMemoryVectorStore

_NOW = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)


def demo_documents() -> list[Document]:
    """三篇贴近真实场景的团队文档。"""
    return [
        Document(
            doc_id="doc-rate-limit",
            title="网关接口限流方案",
            url="https://feishu.cn/docx/doc-rate-limit",
            updated_at=_NOW,
            content=(
                "背景：核心接口在大促期间被上游重试打爆。\n\n"
                "方案：在网关层对单租户做令牌桶限流，默认每分钟 600 次，"
                "超出后返回 429 并携带 Retry-After 头。\n\n"
                "接口频率控制的粒度分三档：按应用、按接口、按用户。"
                "突发流量允许 1.5 倍短时超额，桶容量按峰值 QPS 的 2 倍设置。\n\n"
                "注意事项：限流阈值要靠压测确定，不要凭经验拍数字；"
                "对被限流的调用方必须有明确的错误码与重试指引。"
            ),
        ),
        Document(
            doc_id="doc-deploy",
            title="发布与回滚流程",
            url="https://feishu.cn/docx/doc-deploy",
            updated_at=_NOW,
            content=(
                "发布窗口：工作日 14:00-17:00，禁止在周五与节假日前发布。\n\n"
                "步骤：合并到 main 后自动构建镜像，灰度 5% 观察 10 分钟，"
                "无异常再逐步放量到 100%。\n\n"
                "回滚：任一阶段出现错误率上升，立即执行一键回滚到上一个稳定版本，"
                "回滚不需要审批。回滚后必须补充复盘文档。"
            ),
        ),
        Document(
            doc_id="doc-observability",
            title="可观测性规范",
            url="https://feishu.cn/docx/doc-observability",
            updated_at=_NOW,
            content=(
                "日志：统一输出 JSON lines，必须包含 trace_id，便于跨服务串联。\n\n"
                "指标：每个服务至少暴露请求量、错误率、P95 延迟三个黄金指标。\n\n"
                "告警：告警必须可行动，禁止只报「某某指标异常」而不给出处置指引。"
            ),
        ),
    ]


def build_demo_stack(*, fail_for: list[str] | None = None):
    """构造一套本地实现的服务栈。"""
    source = MockFeishuDocSource(demo_documents(), fail_for=fail_for)
    embedding = LocalHashEmbedding()
    store = InMemoryVectorStore()
    return (
        SearchService(embedding, store),
        IngestService(source, embedding, store),
        store,
    )
