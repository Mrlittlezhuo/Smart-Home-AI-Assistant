"""跨领域场景端到端(T-18):“离家模式”统一规划、确认、执行、汇总。"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph.main_graph import build_graph

ALL_LIGHTS = ["dev-living-main", "dev-living-strip", "dev-bedroom-main", "dev-bedside", "dev-study-desk"]
ALL_CURTAINS = ["dev-living-curtain", "dev-bedroom-curtain"]

SCENE_PLAN = {
    "intent": "device_command", "needs_clarification": False, "clarification_question": None,
    "tasks": [{"task_id": "t1", "domain": "scene", "type": "scene.activate", "target": None,
               "arguments": {"scene_name": "离家模式"},
               "depends_on": [], "condition": None, "delay_seconds": None}],
    "preference_change": None,
}


async def _turns(services, thread: str, confirmed: bool):
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": thread}}
    first = await graph.ainvoke(
        {"session_id": thread, "current_input": "启动离家模式", "fixed_plan": SCENE_PLAN}, config=config)
    interrupts = first.get("__interrupt__") or []
    final = None
    if interrupts:
        final = await graph.ainvoke(Command(resume={"confirmed": confirmed}), config=config)
    return interrupts, final


async def _turn_on_everything(adapter):
    for dev in ALL_LIGHTS:
        await adapter.set_state(dev, {"on": True}, f"scene-seed-{dev}")


async def test_scene_requires_confirmation_then_executes_all(services):
    """T-18:场景必须整体确认;确认后展开为灯光+窗帘两组动作并统一汇总。"""
    _, adapter = services
    await _turn_on_everything(adapter)

    interrupts, final = await _turns(services, "s-scene-1", True)
    assert interrupts, "场景激活必须触发确认"
    assert "离家模式" in interrupts[0].value["prompt"]

    assert "已执行离家模式" in final["response"]
    assert "关闭" in final["response"]
    for dev in ALL_LIGHTS:
        assert (await adapter.get_state(dev)).state["on"] is False, f"{dev} 应已关闭"
    for cur in ALL_CURTAINS:
        assert (await adapter.get_state(cur)).state["position_pct"] == 0, f"{cur} 应已关闭"


async def test_scene_cancel_changes_nothing(services):
    _, adapter = services
    await _turn_on_everything(adapter)

    interrupts, final = await _turns(services, "s-scene-2", False)
    assert interrupts
    assert final["response"] == "已取消该操作。"
    for dev in ALL_LIGHTS:
        assert (await adapter.get_state(dev)).state["on"] is True
    for cur in ALL_CURTAINS:
        assert (await adapter.get_state(cur)).state["position_pct"] == 100


async def test_unknown_scene_reports_error(services):
    """场景不存在:确认后执行失败,回复说明原因,不误报成功。"""
    graph = build_graph(checkpointer=InMemorySaver())
    thread = "s-scene-3"
    config = {"configurable": {"thread_id": thread}}
    plan = {**SCENE_PLAN,
            "tasks": [{**SCENE_PLAN["tasks"][0], "arguments": {"scene_name": "观影模式"}}]}
    first = await graph.ainvoke(
        {"session_id": thread, "current_input": "启动观影模式", "fixed_plan": plan}, config=config)
    final = await graph.ainvoke(Command(resume={"confirmed": True}), config=config)
    assert "不存在" in final["response"]
