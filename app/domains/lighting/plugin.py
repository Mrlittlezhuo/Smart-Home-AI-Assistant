"""灯光领域插件:能力检查、参数范围、领域规则。"""
import re

from app.capabilities.models import ARG_CAPABILITY_MAP, ARG_VALUE_RULES
from app.device_gateway.adapters.base import DeviceInfo
from app.domains.base import DomainPlugin
from app.graph.schemas import PlanTask


class LightingPlugin(DomainPlugin):
    name = "lighting"

    def task_types(self) -> set[str]:
        return {"light.set_state", "light.get_state"}

    def handlers(self) -> dict:
        from app.domains.lighting.handlers import HANDLERS
        return HANDLERS

    def validate_task(self, task: PlanTask, devices: list[DeviceInfo]) -> list[str]:
        errors: list[str] = []
        if task.type == "light.set_state":
            if not task.arguments:
                return ["缺少调节参数"]
            for key, value in task.arguments.items():
                if key not in ARG_CAPABILITY_MAP:
                    errors.append(f"未知参数 {key}")
                    continue
                rule = ARG_VALUE_RULES.get(key, {})
                if rule.get("type") == "number":
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        errors.append(f"{key} 必须是数值")
                        continue
                    if "min" in rule and value < rule["min"] or "max" in rule and value > rule["max"]:
                        errors.append(f"{key}={value} 超出合法范围 [{rule['min']}, {rule['max']}]")
                elif rule.get("type") == "boolean" and not isinstance(value, bool):
                    errors.append(f"{key} 必须是 true/false")
                elif rule.get("type") == "string":
                    if not isinstance(value, str) or not re.match(rule["pattern"], value):
                        errors.append(f"{key}={value!r} 格式不合法")
            # 设备能力检查
            for dev in devices:
                caps = {c.name: c for c in dev.capabilities}
                for key in task.arguments:
                    cap_name, _ = ARG_CAPABILITY_MAP[key]
                    cap = caps.get(cap_name)
                    if cap is None or not cap.writable:
                        errors.append(f"{dev.name} 不支持 {key}")
                    elif key == "brightness_pct" and isinstance(task.arguments[key], (int, float)):
                        if cap.min_value is not None and task.arguments[key] < cap.min_value:
                            errors.append(f"{dev.name} 亮度不能低于 {cap.min_value}%")
                        if cap.max_value is not None and task.arguments[key] > cap.max_value:
                            errors.append(f"{dev.name} 亮度不能高于 {cap.max_value}%")
        elif task.type == "light.get_state":
            pass
        else:
            errors.append(f"未注册的任务类型 {task.type}")
        return errors
