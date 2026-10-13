"""plugins 包：harness 的外部插件（v1.8 附加交付）。

`harness_plugin` 提供会话状态、异步 workflow、源码编辑、shell 自检与回滚，
由 `plugins/harness.shell` 驱动，入口在项目根的 `main.py`。
"""

from __future__ import annotations

from plugins import harness_plugin

__all__ = ["harness_plugin"]
