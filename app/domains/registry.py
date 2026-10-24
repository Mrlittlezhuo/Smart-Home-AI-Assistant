"""领域注册中心:主图通过注册表路由,不硬编码领域(T-17)。"""
from app.domains.base import DomainPlugin
from app.domains.curtain.plugin import CurtainPlugin
from app.domains.lighting.plugin import LightingPlugin
from app.domains.scene.plugin import ScenePlugin


class DomainRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, DomainPlugin] = {}

    def register(self, plugin: DomainPlugin) -> None:
        self._plugins[plugin.name] = plugin

    def get(self, domain: str) -> DomainPlugin | None:
        return self._plugins.get(domain)

    def registered_types(self) -> set[str]:
        out: set[str] = set()
        for p in self._plugins.values():
            out |= p.task_types()
        return out

    def plugins(self) -> list[DomainPlugin]:
        return list(self._plugins.values())


def default_registry() -> DomainRegistry:
    reg = DomainRegistry()
    reg.register(LightingPlugin())
    reg.register(CurtainPlugin())
    reg.register(ScenePlugin())
    return reg
