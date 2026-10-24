"""执行器规则(对应验收 F-04/F-05/F-06、必测 3/4、§6.4 依赖失败停止)。"""
from sqlalchemy import select

from app.db.models import ToolExecutionRow
from app.db.session import get_sessionmaker
from app.graph.nodes.context import context_node
from app.graph.nodes.executor import executor_node
from app.graph.nodes.validator import validator_node
from tests.conftest import make_state


def _task(task_id, device, arguments, depends_on=None, condition=None, delay=None):
    return {
        "task_id": task_id, "domain": "lighting", "type": "light.set_state",
        "target": {"room_name": None, "device_name": device, "device_id": None, "all_home": False},
        "arguments": arguments, "depends_on": depends_on or [], "condition": condition, "delay_seconds": delay,
    }


def _plan(tasks):
    return {"intent": "device_command", "needs_clarification": False,
            "clarification_question": None, "tasks": tasks, "preference_change": None}


async def _run(services, plan: dict, state: dict | None = None) -> dict:
    state = state or make_state(current_input="测试")
    state.update(await context_node(state))
    state["candidate_plan"] = plan  # context 会重置单轮字段,计划必须在其后注入
    out = await validator_node(state)
    state.update(out)
    assert state["validation"]["ok"], state["validation"]
    exec_out = await executor_node(state)
    state.update(exec_out)
    return state


async def test_sequential_order_respected(services):
    _, adapter = services
    await _run(services, _plan([
        _task("t1", "客厅主灯", {"on": True}),
        _task("t2", "客厅主灯", {"brightness_pct": 50}, depends_on=["t1"]),
    ]))
    st = await adapter.get_state("dev-living-main")
    assert st.state["on"] is True
    assert st.state["brightness_pct"] == 50


async def test_partial_success(services):
    svc, adapter = services
    adapter.set_failure_mode("dev-study-desk", "offline")
    state = await _run(services, _plan([
        _task("t1", "客厅主灯", {"on": False}),
        _task("t2", "书房灯", {"on": False}),
    ]))
    by_id = {r["task_id"]: r for r in state["execution_results"]}
    assert by_id["t1"]["status"] == "succeeded"
    assert by_id["t2"]["status"] == "failed"
    assert by_id["t2"]["devices"][0]["error"] == "device_offline"


async def test_dependency_failure_skips(services):
    _, adapter = services
    adapter.set_failure_mode("dev-study-desk", "offline")
    state = await _run(services, _plan([
        _task("t1", "书房灯", {"on": True}),
        _task("t2", "书房灯", {"brightness_pct": 50}, depends_on=["t1"]),
    ]))
    by_id = {r["task_id"]: r for r in state["execution_results"]}
    assert by_id["t1"]["status"] == "failed"
    assert by_id["t2"]["status"] == "skipped"
    assert "dependency_failed" in (by_id["t2"]["error"] or "")


async def test_condition_false_skips(services):
    # 书房灯默认关闭,条件“如果开着”不满足
    state = await _run(services, _plan([
        _task("t1", "书房灯", {"brightness_pct": 30},
              condition={"room_name": None, "device_name": "书房灯", "device_id": None,
                         "field": "on", "equals": True}),
    ]))
    r = state["execution_results"][0]
    assert r["status"] == "skipped"
    assert r["error"] == "condition_false"


async def test_condition_true_executes(services):
    _, adapter = services
    await adapter.set_state("dev-study-desk", {"on": True}, "seed-1")
    state = await _run(services, _plan([
        _task("t1", "书房灯", {"brightness_pct": 30},
              condition={"room_name": None, "device_name": "书房灯", "device_id": None,
                         "field": "on", "equals": True}),
    ]))
    assert state["execution_results"][0]["status"] == "succeeded"
    st = await adapter.get_state("dev-study-desk")
    assert st.state["brightness_pct"] == 30


async def test_idempotent_replay(services):
    """同一计划重复提交,副作用只发生一次(必测 3)。"""
    _, adapter = services
    plan = _plan([_task("t1", "客厅主灯", {"on": True})])
    state = make_state(current_input="测试")
    state.update(await context_node(state))
    state["candidate_plan"] = plan
    state.update(await validator_node(state))
    await executor_node(state)
    # 同一 state(plan_id 不变)再次执行
    await executor_node(state)
    async with get_sessionmaker()() as db:
        rows = (await db.execute(
            select(ToolExecutionRow).where(ToolExecutionRow.plan_id == state["plan_id"])
        )).scalars().all()
    assert len(rows) == 1
    st = await adapter.get_state("dev-living-main")
    assert st.state["on"] is True


async def test_room_group_control(services):
    _, adapter = services
    state = make_state(current_input="关闭客厅灯")
    state.update(await context_node(state))
    state["candidate_plan"] = _plan([{
        "task_id": "t1", "domain": "lighting", "type": "light.set_state",
        "target": {"room_name": "客厅", "device_name": None, "device_id": None, "all_home": False},
        "arguments": {"on": False}, "depends_on": [], "condition": None, "delay_seconds": None,
    }])
    state.update(await validator_node(state))
    assert state["validation"]["ok"]
    assert len(state["validation"]["tasks"][0]["device_ids"]) == 2  # 主灯 + 灯带
    state.update(await executor_node(state))
    for dev in ("dev-living-main", "dev-living-strip"):
        st = await adapter.get_state(dev)
        assert st.state["on"] is False


async def test_delay_creates_scheduled_task(services):
    state = await _run(services, _plan([
        _task("t1", "床头灯", {"on": False}, delay=600),
    ]))
    r = state["execution_results"][0]
    assert r["status"] == "scheduled"
    assert r["delay_seconds"] == 600


async def test_open_plus_delayed_close_not_conflict(services):
    """“打开床头灯,十分钟后关闭”是时间分离操作,不是同时冲突。"""
    state = await _run(services, _plan([
        _task("t1", "床头灯", {"on": True}),
        _task("t2", "床头灯", {"on": False}, delay=600),
    ]))
    by_id = {r["task_id"]: r for r in state["execution_results"]}
    assert by_id["t1"]["status"] == "succeeded"
    assert by_id["t2"]["status"] == "scheduled"
