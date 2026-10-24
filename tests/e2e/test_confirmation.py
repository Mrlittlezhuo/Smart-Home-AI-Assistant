"""interrupt 确认流(对应验收 T-16:确认后使用同一计划恢复,不重新规划)。"""
from sqlalchemy import select
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.db.models import PlanRow
from app.db.session import get_sessionmaker
from app.graph.main_graph import build_graph

ALL_HOME_PLAN = {
    "intent": "device_command", "needs_clarification": False, "clarification_question": None,
    "tasks": [{"task_id": "t1", "domain": "lighting", "type": "light.set_state",
               "target": {"room_name": None, "device_name": None, "device_id": None, "all_home": True},
               "arguments": {"on": False}, "depends_on": [], "condition": None, "delay_seconds": None}],
    "preference_change": None,
}

LONG_DELAY_PLAN = {
    "intent": "device_command", "needs_clarification": False, "clarification_question": None,
    "tasks": [{"task_id": "t1", "domain": "lighting", "type": "light.set_state",
               "target": {"room_name": None, "device_name": "床头灯", "device_id": None, "all_home": False},
               "arguments": {"on": False}, "depends_on": [], "condition": None, "delay_seconds": 7200}],
    "preference_change": None,
}

ALL_DEVICES = ["dev-living-main", "dev-living-strip", "dev-bedroom-main", "dev-bedside", "dev-study-desk"]


def _input(thread: str, message: str, plan: dict) -> dict:
    return {"session_id": thread, "current_input": message, "fixed_plan": plan}


async def _turns(services, thread: str, plan: dict, message: str, confirmed: bool):
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": thread}}
    first = await graph.ainvoke(_input(thread, message, plan), config=config)
    interrupts = first.get("__interrupt__") or []
    final = None
    if interrupts:
        final = await graph.ainvoke(Command(resume={"confirmed": confirmed}), config=config)
    return interrupts, final


async def test_all_home_requires_confirmation_then_confirms(services):
    _, adapter = services
    for dev in ALL_DEVICES:
        await adapter.set_state(dev, {"on": True}, f"seed-{dev}")

    interrupts, final = await _turns(services, "s-conf-1", ALL_HOME_PLAN, "关闭家里所有灯", True)
    assert interrupts, "全屋控制必须触发确认"
    payload = interrupts[0].value
    assert "全屋" in payload["prompt"] or "全部" in payload["prompt"]
    assert payload["plan_hash"]
    assert final is not None and "已关闭" in final["response"]
    for dev in ALL_DEVICES:
        st = await adapter.get_state(dev)
        assert st.state["on"] is False

    # T-16:恢复使用同一计划,没有重新规划
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(PlanRow).where(PlanRow.session_id == "s-conf-1"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "confirmed"


async def test_cancel_does_not_execute(services):
    _, adapter = services
    for dev in ALL_DEVICES:
        await adapter.set_state(dev, {"on": True}, f"seed2-{dev}")

    interrupts, final = await _turns(services, "s-conf-2", ALL_HOME_PLAN, "关闭家里所有灯", False)
    assert interrupts
    assert final["response"] == "已取消该操作。"
    for dev in ALL_DEVICES:
        st = await adapter.get_state(dev)
        assert st.state["on"] is True  # 未执行

    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(PlanRow).where(PlanRow.session_id == "s-conf-2"))).scalars().all()
    assert rows[0].status == "rejected"


async def test_long_delay_requires_confirmation(services):
    """超过一小时的定时任务需要确认(§8.2)。"""
    interrupts, final = await _turns(services, "s-conf-3", LONG_DELAY_PLAN, "两小时后关闭床头灯", True)
    assert interrupts
    assert final["execution_results"][0]["status"] == "scheduled"


async def test_short_delay_no_confirmation(services):
    """普通十分钟定时不需要反复确认。"""
    graph = build_graph(checkpointer=InMemorySaver())
    thread = "s-conf-4"
    plan = {**LONG_DELAY_PLAN, "tasks": [{**LONG_DELAY_PLAN["tasks"][0], "delay_seconds": 600}]}
    result = await graph.ainvoke(_input(thread, "十分钟后关闭床头灯", plan),
                                 config={"configurable": {"thread_id": thread}})
    assert not result.get("__interrupt__")
    assert result["execution_results"][0]["status"] == "scheduled"
