"""端到端主图流程(固定计划路径,阶段一完成标准:
主图路由、检查点保存、工具结果回写、部分成功反馈)。"""
from langgraph.checkpoint.memory import InMemorySaver

from app.graph.main_graph import build_graph
from tests.conftest import make_state


def _task(task_id, device, arguments, depends_on=None):
    return {
        "task_id": task_id, "domain": "lighting", "type": "light.set_state",
        "target": {"room_name": None, "device_name": device, "device_id": None, "all_home": False},
        "arguments": arguments, "depends_on": depends_on or [], "condition": None, "delay_seconds": None,
    }


def _plan(tasks):
    return {"intent": "device_command", "needs_clarification": False,
            "clarification_question": None, "tasks": tasks, "preference_change": None}


async def _chat(svc, session_id, message, fixed_plan):
    graph = build_graph(checkpointer=InMemorySaver())
    return await graph.ainvoke(
        {"session_id": session_id, "current_input": message, "fixed_plan": fixed_plan},
        config={"configurable": {"thread_id": session_id}},
    )


async def test_full_flow_reply_from_facts(services):
    final = await _chat(services[0], "s-e2e-1", "先打开客厅主灯,再把亮度调到50%",
                        _plan([_task("t1", "客厅主灯", {"on": True}),
                               _task("t2", "客厅主灯", {"brightness_pct": 50}, depends_on=["t1"])]))
    assert "已打开客厅主灯" in final["response"]
    assert "亮度调到50%" in final["response"]
    assert final["plan_id"]


async def test_partial_success_reply(services):
    _, adapter = services
    adapter.set_failure_mode("dev-study-desk", "offline")
    final = await _chat(services[0], "s-e2e-2", "关闭客厅主灯和书房灯",
                        _plan([_task("t1", "客厅主灯", {"on": False}),
                               _task("t2", "书房灯", {"on": False})]))
    assert "客厅主灯" in final["response"]
    assert "离线" in final["response"]


async def test_clarification_no_execution(services):
    _, adapter = services
    final = await _chat(services[0], "s-e2e-3", "把灯打开",
                        {"intent": "device_command", "needs_clarification": True,
                         "clarification_question": "你想打开哪个房间的灯?",
                         "tasks": [], "preference_change": None})
    assert final["response"] == "你想打开哪个房间的灯?"
    st = await adapter.get_state("dev-living-main")
    assert st.state["on"] is False  # 未执行任何设备操作


async def test_invalid_plan_no_execution(services):
    _, adapter = services
    final = await _chat(services[0], "s-e2e-4", "把客厅主灯调到150%",
                        _plan([_task("t1", "客厅主灯", {"brightness_pct": 150})]))
    assert "超出合法范围" in final["response"]
    st = await adapter.get_state("dev-living-main")
    assert st.state["brightness_pct"] == 80  # 未变化


async def test_thread_state_not_polluted_across_turns(services):
    """同一线程连续两轮:上一轮的澄清状态不得污染下一轮(检查点回归)。"""
    graph = build_graph(checkpointer=InMemorySaver())
    thread = "s-e2e-6"
    clarify_plan = {"intent": "device_command", "needs_clarification": True,
                    "clarification_question": "你想打开哪个房间的灯?",
                    "tasks": [], "preference_change": None}
    first = await graph.ainvoke(
        {"session_id": thread, "current_input": "把灯打开", "fixed_plan": clarify_plan},
        config={"configurable": {"thread_id": thread}},
    )
    assert first["response"] == "你想打开哪个房间的灯?"
    second = await graph.ainvoke(
        {"session_id": thread, "current_input": "打开客厅主灯",
         "fixed_plan": _plan([_task("t1", "客厅主灯", {"on": True})])},
        config={"configurable": {"thread_id": thread}},
    )
    assert second["response"] == "已打开客厅主灯。"
    assert not second["needs_clarification"]


async def test_status_query(services):
    _, adapter = services
    await adapter.set_state("dev-bedroom-main", {"on": True, "brightness_pct": 30}, "seed-2")
    final = await _chat(services[0], "s-e2e-5", "卧室灯现在开着吗?",
                        {"intent": "status_query", "needs_clarification": False,
                         "clarification_question": None,
                         "tasks": [{"task_id": "t1", "domain": "lighting", "type": "light.get_state",
                                    "target": {"room_name": None, "device_name": "卧室主灯",
                                               "device_id": None, "all_home": False},
                                    "arguments": {}, "depends_on": [], "condition": None, "delay_seconds": None}],
                         "preference_change": None})
    assert "开着" in final["response"]
    assert "30%" in final["response"]
