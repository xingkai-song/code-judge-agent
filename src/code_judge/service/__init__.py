"""HTTP 服务层：把核心库包成一个浏览器可用的后端。

这一层只做三件事：加载一次题库、给每个用户一个陪练会话、把判题放到后台线程。
判题、诊断、掌握度这些逻辑全部复用核心库，服务层不重新实现任何业务规则。
"""

from __future__ import annotations

from code_judge.service.app import create_app
from code_judge.service.runtime import ServiceRuntime
from code_judge.service.tasks import TaskQueue

__all__ = ["ServiceRuntime", "TaskQueue", "create_app"]
