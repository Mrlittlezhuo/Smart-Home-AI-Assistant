"""统一设备能力模型(§6.9)。

上层只使用标准能力名,不接触厂商字段。
工具参数 → 标准能力的映射在这里集中定义,供校验器和领域插件共用。
"""
from pydantic import BaseModel

CAP_ON_OFF = "on_off"
CAP_BRIGHTNESS = "brightness"
CAP_COLOR_TEMP = "color_temp"
CAP_COLOR = "color"
CAP_POSITION = "position"

# 工具参数名 -> (标准能力, 参数取值键)
ARG_CAPABILITY_MAP: dict[str, tuple[str, str]] = {
    "on": (CAP_ON_OFF, "on"),
    "brightness_pct": (CAP_BRIGHTNESS, "brightness_pct"),
    "color_temp_k": (CAP_COLOR_TEMP, "color_temp_k"),
    "color_hex": (CAP_COLOR, "color_hex"),
    "position_pct": (CAP_POSITION, "position_pct"),
}

# 参数合法范围(设备能力范围之外的进一步约束由设备能力给出)
ARG_VALUE_RULES: dict[str, dict] = {
    "brightness_pct": {"min": 0, "max": 100, "type": "number"},
    "color_temp_k": {"min": 1700, "max": 6500, "type": "number"},
    "color_hex": {"type": "string", "pattern": r"^#[0-9A-Fa-f]{6}$"},
    "on": {"type": "boolean"},
    "position_pct": {"min": 0, "max": 100, "type": "number"},
}


class Capability(BaseModel):
    name: str
    min_value: float | None = None
    max_value: float | None = None
    unit: str | None = None
    readable: bool = True
    writable: bool = True
