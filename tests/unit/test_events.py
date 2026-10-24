"""事件管道与自动化(T-19):去重、状态更新、按启用规则确定性触发。"""
from sqlalchemy import func, select

from app.db.models import AuditEventRow, DeviceEventRow, DeviceStateRow
from app.db.session import get_sessionmaker


async def _event_rows(event_id: str) -> int:
    async with get_sessionmaker()() as db:
        return (await db.execute(
            select(func.count()).select_from(DeviceEventRow).where(DeviceEventRow.event_id == event_id)
        )).scalar()


async def _audit_count() -> int:
    async with get_sessionmaker()() as db:
        return (await db.execute(
            select(func.count()).select_from(AuditEventRow).where(AuditEventRow.action == "automation.triggered")
        )).scalar()


async def test_motion_event_triggers_automation(services):
    _, adapter = services
    assert (await adapter.get_state("dev-living-main")).state["on"] is False  # 前置条件成立

    await adapter.inject_event("dev-living-motion", "motion_detected", event_id="evt-t19")

    # 已启用自动化:客厅有人且灯未开 → 开灯
    assert (await adapter.get_state("dev-living-main")).state["on"] is True
    assert await _audit_count() == 1
    # 设备状态表已按事件更新(带观测时间)
    async with get_sessionmaker()() as db:
        row = await db.get(DeviceStateRow, "dev-living-main")
    assert row is not None and row.state.get("on") is True


async def test_duplicate_event_id_is_deduped(services):
    """同一 event_id 重复上报:只处理一次,不重复触发副作用。"""
    _, adapter = services
    await adapter.inject_event("dev-living-motion", "motion_detected", event_id="evt-dup")
    await adapter.inject_event("dev-living-motion", "motion_detected", event_id="evt-dup")
    assert await _event_rows("evt-dup") == 1
    assert await _audit_count() == 1


async def test_precondition_blocks_retrigger(services):
    """确定性条件:灯已开时再次有人事件不再触发开灯。"""
    _, adapter = services
    await adapter.inject_event("dev-living-motion", "motion_detected", event_id="evt-a")
    await adapter.inject_event("dev-living-motion", "motion_detected", event_id="evt-b")
    assert await _event_rows("evt-b") == 1  # 事件本身入库
    assert await _audit_count() == 1        # 但自动化只触发了一次


async def test_unmatched_event_only_updates_state(services):
    """无匹配规则的事件:仅记录与更新状态,不产生动作。"""
    _, adapter = services
    await adapter.set_state("dev-living-main", {"on": True, "brightness_pct": 60}, "seed-ev")
    await adapter.inject_event("dev-living-main", "state_changed", event_id="evt-none")
    assert await _audit_count() == 0
    async with get_sessionmaker()() as db:
        row = await db.get(DeviceStateRow, "dev-living-main")
    assert row.state.get("brightness_pct") == 60
