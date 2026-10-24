"""校验器边界用例(对应验收 F-02/F-08/F-09/F-13、必测 1/2/5)。"""
from tests.conftest import make_state

from app.graph.nodes.context import context_node
from app.graph.nodes.validator import validator_node


async def _validate(services, plan: dict) -> dict:
    state = make_state(current_input="测试")
    state.update(await context_node(state))
    state["candidate_plan"] = plan  # context 会重置单轮字段,计划必须在其后注入
    out = await validator_node(state)
    return out["validation"]


def _plan(tasks, **kw) -> dict:
    base = {"intent": "device_command", "needs_clarification": False,
            "clarification_question": None, "tasks": tasks, "preference_change": None}
    base.update(kw)
    return base


async def test_brightness_out_of_range_rejected(services):
    v = await _validate(services, _plan([{
        "task_id": "t1", "domain": "lighting", "type": "light.set_state",
        "target": {"room_name": "卧室", "device_name": None, "device_id": None, "all_home": False},
        "arguments": {"brightness_pct": 150}, "depends_on": [], "condition": None, "delay_seconds": None,
    }]))
    assert not v["ok"]
    assert any("超出合法范围" in e for e in v["errors"])


async def test_unsupported_capability(services):
    # 床头灯不支持色温
    v = await _validate(services, _plan([{
        "task_id": "t1", "domain": "lighting", "type": "light.set_state",
        "target": {"room_name": None, "device_name": "床头灯", "device_id": None, "all_home": False},
        "arguments": {"color_temp_k": 3000}, "depends_on": [], "condition": None, "delay_seconds": None,
    }]))
    assert not v["ok"]
    assert any("不支持" in e for e in v["errors"])


async def test_unregistered_task_type_rejected(services):
    v = await _validate(services, _plan([{
        "task_id": "t1", "domain": "lighting", "type": "light.detonate",
        "target": {"room_name": "客厅", "device_name": None, "device_id": None, "all_home": False},
        "arguments": {}, "depends_on": [], "condition": None, "delay_seconds": None,
    }]))
    assert not v["ok"]
    assert any("未注册" in e for e in v["errors"])


async def test_missing_target_clarifies(services):
    v = await _validate(services, _plan([{
        "task_id": "t1", "domain": "lighting", "type": "light.set_state",
        "target": None, "arguments": {"on": True},
        "depends_on": [], "condition": None, "delay_seconds": None,
    }]))
    assert not v["ok"]
    assert v["clarifications"]


async def test_unknown_device_clarifies(services):
    v = await _validate(services, _plan([{
        "task_id": "t1", "domain": "lighting", "type": "light.set_state",
        "target": {"room_name": None, "device_name": "阳台灯", "device_id": None, "all_home": False},
        "arguments": {"on": True}, "depends_on": [], "condition": None, "delay_seconds": None,
    }]))
    assert not v["ok"]
    assert any("没有找到" in c for c in v["clarifications"])


async def test_conflicting_commands_clarify(services):
    v = await _validate(services, _plan([
        {"task_id": "t1", "domain": "lighting", "type": "light.set_state",
         "target": {"room_name": None, "device_name": "客厅主灯", "device_id": None, "all_home": False},
         "arguments": {"on": True}, "depends_on": [], "condition": None, "delay_seconds": None},
        {"task_id": "t2", "domain": "lighting", "type": "light.set_state",
         "target": {"room_name": None, "device_name": "客厅主灯", "device_id": None, "all_home": False},
         "arguments": {"on": False}, "depends_on": [], "condition": None, "delay_seconds": None},
    ]))
    assert not v["ok"]
    assert any("打开和关闭" in c for c in v["clarifications"])


async def test_plan_needs_clarification_short_circuits(services):
    v = await _validate(services, _plan([], needs_clarification=True, clarification_question="你想打开哪个房间的灯?"))
    assert not v["ok"]
    assert v["clarifications"] == ["你想打开哪个房间的灯?"]
