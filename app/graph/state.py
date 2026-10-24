"""WholeHomeAgentState(§6.4)。

State 只保存节点间共享/恢复所需的原始数据,不保存拼接好的提示词。
"""
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # 会话消息(检查点持久化,用于连续对话)
    messages: Annotated[list, add_messages]

    # 身份与输入
    user_id: str
    home_id: str
    timezone: str
    session_id: str
    current_input: str
    # 测试/阶段一:跳过 LLM 的固定候选计划
    fixed_plan: dict[str, Any] | None

    # 上下文
    device_catalog: list[dict]
    scenes: list[str]
    recent_turns: list[dict]
    recent_targets: list[dict]
    # 个人偏好(§9):按用户加载,可被请求级/总开关关闭
    use_preferences: bool
    preference_enabled: bool
    user_preferences: list[dict]

    # 规划与校验
    candidate_plan: dict[str, Any] | None
    plan_id: str
    plan_hash: str
    validation: dict[str, Any] | None

    # 确认(§11.2 interrupt)
    approval_state: str | None

    # 执行与回复
    execution_results: list[dict]
    needs_clarification: bool
    clarification_question: str | None
    response: str | None
