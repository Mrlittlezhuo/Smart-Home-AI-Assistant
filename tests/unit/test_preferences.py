"""个人偏好(对应验收 F-15/F-16/F-17/F-18/F-19、T-08/T-09/T-10)。"""
from sqlalchemy import select

from app.db.models import PreferenceRow
from app.db.session import get_sessionmaker
from app.graph.nodes.context import context_node
from app.graph.nodes.executor import executor_node
from app.graph.nodes.validator import validator_node
from tests.conftest import make_state


def _plan(tasks=None, pref=None, intent="device_command"):
    return {"intent": intent, "needs_clarification": False, "clarification_question": None,
            "tasks": tasks or [], "preference_change": pref}


def _set_on_task(device):
    return {"task_id": "t1", "domain": "lighting", "type": "light.set_state",
            "target": {"room_name": None, "device_name": device, "device_id": None, "all_home": False},
            "arguments": {"on": True}, "depends_on": [], "condition": None, "delay_seconds": None}


async def _run(services, plan: dict, user_id: str = "u1", use_preferences: bool = True) -> dict:
    state = make_state(current_input="测试", user_id=user_id, use_preferences=use_preferences)
    state.update(await context_node(state))
    state["candidate_plan"] = plan
    state.update(await validator_node(state))
    assert state["validation"]["ok"], state["validation"]
    state.update(await executor_node(state))
    return state


async def _prefs(user_id: str = "u1") -> list[PreferenceRow]:
    async with get_sessionmaker()() as db:
        return list((await db.execute(
            select(PreferenceRow).where(PreferenceRow.user_id == user_id,
                                        PreferenceRow.enabled.is_(True))
        )).scalars().all())


async def test_explicit_remember_saves_preference(services):
    """F-16:用户明确说“记住”才写入,来源记为 explicit_chat。"""
    await _run(services, _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    ))
    prefs = await _prefs()
    assert len(prefs) == 1
    assert prefs[0].source == "explicit_chat"
    assert prefs[0].value == 30
    assert prefs[0].scope_type == "device" and prefs[0].scope_id == "dev-bedroom-main"


async def test_single_operation_does_not_save(services):
    """F-15:一次亮度调整不自动保存为偏好。"""
    await _run(services, _plan([{**_set_on_task("卧室灯"), "arguments": {"on": True, "brightness_pct": 30}}]))
    assert await _prefs() == []


async def test_preference_completes_missing_argument(services):
    """偏好补全未说明的参数,并记录参数来源(§9.3)。"""
    await _run(services, _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    ))
    state = await _run(services, _plan([_set_on_task("卧室灯")]))
    r = state["execution_results"][0]
    assert r["status"] == "succeeded"
    assert r["arguments"]["brightness_pct"] == 30
    assert "preference" in r["argument_sources"]["brightness_pct"]
    _, adapter = services
    st = await adapter.get_state("dev-bedroom-main")
    assert st.state["brightness_pct"] == 30


async def test_current_instruction_overrides_preference(services):
    """F-17/T-09:当前明确指令覆盖偏好,且不修改原偏好。"""
    await _run(services, _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    ))
    task = {**_set_on_task("卧室灯"), "arguments": {"on": True, "brightness_pct": 70}}
    state = await _run(services, _plan([task]))
    _, adapter = services
    st = await adapter.get_state("dev-bedroom-main")
    assert st.state["brightness_pct"] == 70
    prefs = await _prefs()
    assert prefs[0].value == 30  # 原偏好不变
    assert "argument_sources" not in state["execution_results"][0]


async def test_preference_isolated_per_user(services):
    """F-18/T-08:偏好按用户隔离。"""
    await _run(services, _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    ), user_id="u2")
    # u1 不受 u2 偏好影响
    state = await _run(services, _plan([_set_on_task("卧室灯")]), user_id="u1")
    r = state["execution_results"][0]
    assert "brightness_pct" not in r["arguments"]
    assert await _prefs("u1") == []


async def test_use_preferences_false_skips_injection(services):
    await _run(services, _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    ))
    state = await _run(services, _plan([_set_on_task("卧室灯")]), use_preferences=False)
    assert "brightness_pct" not in state["execution_results"][0]["arguments"]


async def test_delete_preference_stops_injection(services):
    """T-10:删除后不再注入上下文与补全。"""
    await _run(services, _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    ))
    await _run(services, _plan(
        pref={"action": "delete", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": None},
        intent="preference_change",
    ))
    assert await _prefs() == []
    state = await _run(services, _plan([_set_on_task("卧室灯")]))
    assert "brightness_pct" not in state["execution_results"][0]["arguments"]


async def test_invalid_preference_value_rejected(services):
    """T-08:非法偏好值不写入。"""
    state = make_state(current_input="测试")
    state.update(await context_node(state))
    state["candidate_plan"] = _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "卧室灯", "preference_key": "default_brightness", "value": 150},
        intent="preference_change",
    )
    state.update(await validator_node(state))
    assert not state["validation"]["ok"]
    assert any("超出合法范围" in e for e in state["validation"]["errors"])
    assert await _prefs() == []


async def test_master_switch_off_blocks_load_and_write(services):
    """F-19:关闭偏好功能后,不加载也不新增。"""
    from app.db.models import UserSettingRow

    async with get_sessionmaker()() as db:
        db.add(UserSettingRow(user_id="u1", preference_enabled=False))
        await db.commit()

    # 不加载:先造一条已存在的偏好,确认不注入
    async with get_sessionmaker()() as db:
        db.add(PreferenceRow(preference_id="p-off", user_id="u1", home_id="h1",
                             scope_type="device", scope_id="dev-bedroom-main", scope_name="卧室主灯",
                             preference_key="default_brightness", value=30, enabled=True))
        await db.commit()
    state = await _run(services, _plan([_set_on_task("卧室灯")]))
    assert "brightness_pct" not in state["execution_results"][0]["arguments"]

    # 不新增
    st = make_state(current_input="记住偏好")
    st.update(await context_node(st))
    st["candidate_plan"] = _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "书房灯", "preference_key": "default_brightness", "value": 40},
        intent="preference_change",
    )
    st.update(await validator_node(st))
    assert not st["validation"]["ok"]
    assert any("已关闭" in e for e in st["validation"]["errors"])


async def test_preference_for_unknown_device_rejected(services):
    state = make_state(current_input="测试")
    state.update(await context_node(state))
    state["candidate_plan"] = _plan(
        pref={"action": "set", "scope_type": "device", "room_name": None,
              "device_name": "阳台灯", "preference_key": "default_brightness", "value": 30},
        intent="preference_change",
    )
    state.update(await validator_node(state))
    assert not state["validation"]["ok"]
    assert any("没有找到" in e for e in state["validation"]["errors"])
