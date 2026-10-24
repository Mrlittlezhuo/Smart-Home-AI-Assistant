"""TaskPlan 结构化候选计划 Schema(§7.1)。

LLM 输出的是候选计划,必须经过程序校验后才成为可执行计划。
"""
from typing import Any, Literal

from pydantic import BaseModel, Field

# 领域是否注册由校验器按注册表判断(§6.6),Schema 不做枚举限制,
# 新增领域不需要改 Schema(T-17)。


class TaskTarget(BaseModel):
    room_name: str | None = None
    device_name: str | None = None
    device_id: str | None = None
    # 全屋控制,例如“关闭家里所有灯”
    all_home: bool = False


class TaskCondition(BaseModel):
    """条件执行:执行前用状态查询工具实时判断(§6.3 条件执行)。"""

    room_name: str | None = None
    device_name: str | None = None
    device_id: str | None = None
    field: Literal["on", "brightness_pct", "online"]
    equals: Any


class PlanTask(BaseModel):
    task_id: str
    domain: str
    # light.set_state / curtain.set_position / scene.activate / conversation.reply / ...
    type: str
    target: TaskTarget | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    condition: TaskCondition | None = None
    delay_seconds: int | None = None


class PreferenceChange(BaseModel):
    """偏好变更(§9.2)。只有用户明确说“记住/以后默认”等才允许出现。"""

    action: Literal["set", "delete", "list"]
    scope_type: Literal["user", "room", "device"] | None = None
    room_name: str | None = None
    device_name: str | None = None
    preference_key: Literal["default_brightness", "default_color_temp", "alias"] | None = None
    value: Any = None


class TaskPlan(BaseModel):
    intent: Literal[
        "device_command", "status_query", "conversation", "mixed", "task_control", "preference_change"
    ]
    needs_clarification: bool = False
    clarification_question: str | None = None
    tasks: list[PlanTask] = Field(default_factory=list)
    preference_change: PreferenceChange | None = None
