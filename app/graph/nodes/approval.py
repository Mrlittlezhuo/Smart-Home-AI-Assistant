"""确认节点(§11.2 / T-16):用 LangGraph interrupt 暂停线程等待用户确认。

确认后以同一 thread_id、同一计划恢复,不重新调用 LLM 规划。
"""
from langgraph.types import interrupt

from app.db.models import PlanRow
from app.db.session import get_sessionmaker
from app.graph.state import AgentState


async def approval_node(state: AgentState) -> dict:
    validation = state["validation"]
    decision = interrupt({
        "plan_id": state["plan_id"],
        "plan_hash": state["plan_hash"],
        "prompt": validation.get("confirmation_prompt"),
    })
    confirmed = bool(decision.get("confirmed")) if isinstance(decision, dict) else bool(decision)

    sm = get_sessionmaker()
    async with sm() as db:
        row = await db.get(PlanRow, state["plan_id"])
        if row is not None:
            row.status = "confirmed" if confirmed else "rejected"
            await db.commit()

    if confirmed:
        return {"approval_state": "confirmed"}
    return {"approval_state": "rejected", "response": "已取消该操作。"}
