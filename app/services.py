"""进程级服务容器:API 与 Worker 共用同一套图、领域与工具模块。"""
from dataclasses import dataclass, field

from app.device_gateway.gateway import DeviceGateway
from app.domains.registry import DomainRegistry, default_registry
from app.llm.gateway import LLMGateway
from app.tools.registry import ToolRegistry, default_tool_registry


@dataclass
class Services:
    gateway: DeviceGateway
    llm: LLMGateway
    domains: DomainRegistry = field(default_factory=default_registry)
    tools: ToolRegistry = field(default_factory=lambda: default_tool_registry(default_registry()))
    graph: object | None = None  # 编译后的 LangGraph

    def __post_init__(self) -> None:
        # 保证工具处理器与领域注册一致
        self.tools = default_tool_registry(self.domains)


_services: Services | None = None


def set_services(s: Services) -> None:
    global _services
    _services = s


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("services not initialized")
    return _services
