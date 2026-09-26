"""测试夹具：把 kb-search 加入 import 路径。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def demo_stack():
    from kb_search.demo_data import build_demo_stack

    return build_demo_stack()


@pytest.fixture
def synced_stack():
    """已同步好演示语料的 (search, ingest, store)。"""
    from kb_search.demo_data import build_demo_stack

    search, ingest, store = build_demo_stack()
    ingest.sync()
    return search, ingest, store
