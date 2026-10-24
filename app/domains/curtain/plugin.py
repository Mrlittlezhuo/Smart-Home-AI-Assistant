"""窗帘领域插件(§6.3:理解开合比例)。第二个模拟领域,验证 T-17。"""
from app.capabilities.models import ARG_VALUE_RULES, CAP_POSITION
from app.device_gateway.adapters.base import DeviceInfo
from app.domains.base import DomainPlugin
from app.graph.schemas import PlanTask


class CurtainPlugin(DomainPlugin):
    name = "curtain"

    def task_types(self) -> set[str]:
        return {"curtain.set_position", "curtain.get_state"}

    def validate_task(self, task: PlanTask, devices: list[DeviceInfo]) -> list[str]:
        errors: list[str] = []
        if task.type == "curtain.set_position":
            if not task.arguments:
                return ["缺少开合参数"]
            rule = ARG_VALUE_RULES["position_pct"]
            for key, value in task.arguments.items():
                if key != "position_pct":
                    errors.append(f"未知参数 {key}")
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    errors.append("position_pct 必须是数值")
                elif not rule["min"] <= value <= rule["max"]:
                    errors.append(f"position_pct={value} 超出合法范围 [0, 100]")
            for dev in devices:
                if not any(c.name == CAP_POSITION and c.writable for c in dev.capabilities):
                    errors.append(f"{dev.name} 不支持开合控制")
        elif task.type == "curtain.get_state":
            pass
        else:
            errors.append(f"未注册的任务类型 {task.type}")
        return errors

    def handlers(self) -> dict:
        from app.domains.curtain.handlers import HANDLERS
        return HANDLERS
