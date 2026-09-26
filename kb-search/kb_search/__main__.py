"""命令行入口：同步 + 检索演示。

用法：
    python -m kb_search                 # 用演示语料同步一次，再跑几个示例查询
    python -m kb_search "接口限流"        # 指定查询词
    python -m kb_search --top-k 3 "发布流程"
    python -m kb_search --serve         # 启动 HTTP 服务（需要 fastapi/uvicorn）
"""

from __future__ import annotations

import argparse
import sys

from kb_search.demo_data import build_demo_stack


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


DEFAULT_QUERIES = ["接口限流怎么做", "发布回滚", "告警要怎么写", "报销流程"]


def run_demo(queries: list[str], top_k: int) -> int:
    search_service, ingest_service, store = build_demo_stack()

    print("=" * 62)
    print("① 同步：从飞书（Mock）拉取文档 → 切分 → 向量化 → 入库")
    print("=" * 62)
    report = ingest_service.sync()
    print(f"  文档总数 {report.total}，成功 {report.succeeded}，"
          f"跳过 {report.skipped}，失败 {report.failed}")
    print(f"  写入片段 {report.chunks_written} 个，向量库现有 {store.count()} 个片段")

    print()
    print("=" * 62)
    print("② 检索：语义近邻搜索")
    print("=" * 62)
    for query in queries:
        hits = search_service.search(query, top_k)
        print(f"\n  查询：{query!r}  → 命中 {len(hits)} 条")
        for rank, hit in enumerate(hits, 1):
            preview = hit.text.replace("\n", " ")[:56]
            print(f"    {rank}. [{hit.score:.4f}] {hit.title} — {preview}…")
        if not hits:
            print("    （无命中）")

    print()
    print("=" * 62)
    print("③ 增量同步：内容未变更应被跳过")
    print("=" * 62)
    again = ingest_service.sync()
    print(f"  跳过 {again.skipped}/{again.total}，新写入片段 {again.chunks_written} 个")
    return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="知识库语义搜索工具（演示）")
    parser.add_argument("query", nargs="*", help="查询词；留空则跑默认示例查询")
    parser.add_argument("--top-k", type=int, default=3, help="返回条数，默认 3")
    parser.add_argument("--serve", action="store_true", help="启动 HTTP 服务")
    args = parser.parse_args(argv)

    if args.serve:
        import uvicorn

        uvicorn.run("kb_search.api:create_demo_app", factory=True, host="127.0.0.1", port=8000)
        return 0

    queries = args.query or DEFAULT_QUERIES
    return run_demo(queries, args.top_k)


if __name__ == "__main__":
    sys.exit(main())
