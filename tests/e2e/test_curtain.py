"""窗帘领域端到端(T-17):新领域通过注册表接入主图,不改主图与执行器。"""
from langgraph.checkpoint.memory import InMemorySaver

from app.graph.main_graph import build_graph


def _task(task_id, domain, task_type, target, arguments):
    return {"task_id": task_id, "domain": domain, "type": task_type, "target": target,
            "arguments": arguments, "depends_on": [], "condition": None, "delay_seconds": None}


def _plan(tasks):
    return {"intent": "device_command", "needs_clarification": False,
            "clarification_question": None, "tasks": tasks, "preference_change": None}


def _target(device_name=None, room_name=None):
    return {"room_name": room_name, "device_name": device_name, "device_id": None, "all_home": False}


async def _chat(svc, session_id, message, fixed_plan):
    graph = build_graph(checkpointer=InMemorySaver())
    return await graph.ainvoke(
        {"session_id": session_id, "current_input": message, "fixed_plan": fixed_plan},
        config={"configurable": {"thread_id": session_id}},
    )


async def test_curtain_half_open(services):
    _, adapter = services
    final = await _chat(services[0], "s-curtain-1", "把客厅窗帘打开一半",
                        _plan([_task("t1", "curtain", "curtain.set_position",
                                     _target("客厅窗帘"), {"position_pct": 50})]))
    assert "打开到50%" in final["response"]
    assert (await adapter.get_state("dev-living-curtain")).state["position_pct"] == 50
    # 卧室窗帘不受影响
    assert (await adapter.get_state("dev-bedroom-curtain")).state["position_pct"] == 100


async def test_curtain_room_group_close(services):
    _, adapter = services
    final = await _chat(services[0], "s-curtain-2", "关闭卧室的窗帘",
                        _plan([_task("t1", "curtain", "curtain.set_position",
                                     _target(room_name="卧室"), {"position_pct": 0})]))
    assert "已关闭卧室窗帘" in final["response"]
    assert (await adapter.get_state("dev-bedroom-curtain")).state["position_pct"] == 0


async def test_lighting_task_rejected_for_curtain(services):
    """任务类型按领域注册校验:窗帘领域不接受 light.set_state。"""
    _, adapter = services
    final = await _chat(services[0], "s-curtain-3", "把客厅窗帘打开",
                        _plan([_task("t1", "curtain", "light.set_state",
                                     _target("客厅窗帘"), {"on": True})]))
    assert "未能执行" in final["response"]
    assert (await adapter.get_state("dev-living-curtain")).state["position_pct"] == 100


async def test_unregistered_domain_rejected(services):
    """未注册领域被校验器拒绝,不产生任何副作用(T-17 反向验证)。"""
    final = await _chat(services[0], "s-curtain-4", "把空调调到26度",
                        _plan([_task("t1", "climate", "climate.set_temperature",
                                     _target("客厅空调"), {"temperature": 26})]))
    assert "未注册的领域" in final["response"]
