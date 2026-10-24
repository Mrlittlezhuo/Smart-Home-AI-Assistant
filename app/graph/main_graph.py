"""全屋智能主图(§6.2):所有领域共享的稳定骨架。

context → planner →(澄清/失败→responder)| validator →(不通过→responder)| executor → responder
主图不包含灯光等业务细节;领域规则经注册插件进入。
"""
from langgraph.graph import END, StateGraph

from app.graph.nodes.approval import approval_node
from app.graph.nodes.context import context_node
from app.graph.nodes.executor import executor_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.responder import responder_node
from app.graph.nodes.validator import validator_node
from app.graph.state import AgentState


def _after_planner(state: AgentState) -> str:
    if state.get("response") or state.get("candidate_plan") is None:
        return "responder"
    if state.get("needs_clarification"):
        return "responder"
    return "validator"


def _after_validator(state: AgentState) -> str:
    validation = state.get("validation") or {}
    if not validation.get("ok"):
        return "responder"
    if validation.get("needs_confirmation"):
        return "approval"
    return "executor"


def _after_approval(state: AgentState) -> str:
    return "executor" if state.get("approval_state") == "confirmed" else "responder"


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("context", context_node)
    g.add_node("planner", planner_node)
    g.add_node("validator", validator_node)
    g.add_node("approval", approval_node)
    g.add_node("executor", executor_node)
    g.add_node("responder", responder_node)

    g.set_entry_point("context")
    g.add_edge("context", "planner")
    g.add_conditional_edges("planner", _after_planner, {"validator": "validator", "responder": "responder"})
    g.add_conditional_edges(
        "validator", _after_validator,
        {"executor": "executor", "approval": "approval", "responder": "responder"},
    )
    g.add_conditional_edges("approval", _after_approval, {"executor": "executor", "responder": "responder"})
    g.add_edge("executor", "responder")
    g.add_edge("responder", END)
    return g.compile(checkpointer=checkpointer)
