"""工具注册中心(§6.8):工具元数据 + 任务类型 → 处理器映射。

处理器由各领域插件与公共工具注册;执行器只按名称分发,不认识领域。
"""
from pydantic import BaseModel


class ToolSpec(BaseModel):
    name: str
    side_effect: bool
    timeout_s: float = 10.0
    retryable: bool = False


_BUILTIN_SPECS = [
    ToolSpec(name="light.resolve", side_effect=False),
    ToolSpec(name="light.get_state", side_effect=False),
    ToolSpec(name="light.set_state", side_effect=True, retryable=True),
    ToolSpec(name="curtain.get_state", side_effect=False),
    ToolSpec(name="curtain.set_position", side_effect=True, retryable=True),
    ToolSpec(name="scene.activate", side_effect=True),
    ToolSpec(name="task.create", side_effect=True),
    ToolSpec(name="task.get", side_effect=False),
    ToolSpec(name="task.cancel", side_effect=True),
]


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs = {s.name: s for s in (specs or _BUILTIN_SPECS)}
        self._handlers: dict[str, object] = {}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def has(self, name: str) -> bool:
        return name in self._specs

    def register_handler(self, task_type: str, handler) -> None:
        self._handlers[task_type] = handler

    def handler_for(self, task_type: str):
        return self._handlers.get(task_type)


def default_tool_registry(domains) -> ToolRegistry:
    """从领域插件收集处理器,再注册公共工具(闲聊、定时任务查询/取消)。"""
    from app.tools.common import run_conversation, run_task_cancel, run_task_get

    reg = ToolRegistry()
    for plugin in domains.plugins():
        for task_type, handler in plugin.handlers().items():
            reg.register_handler(task_type, handler)
    reg.register_handler("conversation.reply", run_conversation)
    reg.register_handler("task.get", run_task_get)
    reg.register_handler("task.cancel", run_task_cancel)
    return reg
