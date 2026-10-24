"""场景领域插件(§6.3):协调跨领域动作,例如“离家模式”。

场景定义存 scenes 表;激活时在执行器内展开为各领域的已注册动作,
统一走同一套校验后的工具链,不绕过安全边界。
"""
from app.device_gateway.adapters.base import DeviceInfo
from app.domains.base import DomainPlugin
from app.graph.schemas import PlanTask


class ScenePlugin(DomainPlugin):
    name = "scene"
    needs_devices = False

    def task_types(self) -> set[str]:
        return {"scene.activate"}

    def validate_task(self, task: PlanTask, devices: list[DeviceInfo]) -> list[str]:
        if not task.arguments.get("scene_name"):
            return ["缺少场景名称"]
        return []

    def handlers(self) -> dict:
        from app.domains.scene.handlers import HANDLERS
        return HANDLERS
