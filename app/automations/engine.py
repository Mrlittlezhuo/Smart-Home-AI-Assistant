"""事件接入与自动化触发(§4.3 / §5.5,T-19)。

流程:事件去重 → 更新带时间戳的设备状态 → 匹配已启用规则 →
确定性条件检查 → 通过工具执行已批准动作(幂等键含事件 ID,重复事件不重复触发)。
自动化不让 LLM 临时生成动作。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

import app.services
from app.db.models import AuditEventRow, AutomationRow, DeviceEventRow, DeviceStateRow
from app.db.session import get_sessionmaker
from app.device_gateway.adapters.base import DeviceEvent
from app.tools.common import idempotency_key

logger = logging.getLogger(__name__)


async def handle_event(event: DeviceEvent) -> None:
    sm = get_sessionmaker()
    # 1) 事件去重(§4.3 第2步)
    async with sm() as db:
        if await db.get(DeviceEventRow, event.event_id) is not None:
            logger.debug("duplicate event %s ignored", event.event_id)
            return
        db.add(DeviceEventRow(event_id=event.event_id, device_id=event.device_id,
                              event_type=event.event_type, payload=event.payload,
                              occurred_at=event.occurred_at))
        await db.commit()

    # 2) 更新设备状态(带观测时间)
    svc = app.services.get_services()
    try:
        st = await svc.gateway.get_state(event.device_id)
        async with sm() as db:
            row = await db.get(DeviceStateRow, event.device_id)
            if row is None:
                db.add(DeviceStateRow(device_id=event.device_id, online=st.online,
                                      state=st.state, observed_at=st.observed_at))
            else:
                row.online, row.state, row.observed_at = st.online, st.state, st.observed_at
            await db.commit()
    except KeyError:
        pass  # 传感器事件设备可能无状态

    # 3) 匹配已启用自动化 + 确定性条件检查
    async with sm() as db:
        rows = (
            await db.execute(
                select(AutomationRow).where(AutomationRow.enabled.is_(True))
            )
        ).scalars().all()
    for auto in rows:
        trig = auto.trigger
        if trig.get("event_type") != event.event_type:
            continue
        if trig.get("device_id") and trig["device_id"] != event.device_id:
            continue
        if not await _check_preconditions(auto.preconditions):
            continue
        await _run_actions(auto, event)


async def _check_preconditions(preconditions: list[dict]) -> bool:
    svc = app.services.get_services()
    for cond in preconditions or []:
        try:
            st = await svc.gateway.get_state(cond["device_id"])
        except KeyError:
            return False
        actual = st.online if cond["field"] == "online" else st.state.get(cond["field"])
        if actual != cond["equals"]:
            return False
    return True


async def _run_actions(auto: AutomationRow, event: DeviceEvent) -> None:
    svc = app.services.get_services()
    sm = get_sessionmaker()
    executed = []
    for i, action in enumerate(auto.actions or []):
        handler = svc.tools.handler_for(action["type"])
        if handler is None:
            continue
        task = {
            "task_id": f"auto-{event.event_id}-{i}",
            "type": action["type"],
            "arguments": action["arguments"],
            "device_ids": action.get("device_ids") or [],
            "device_names": action.get("device_names") or [],
        }
        # 幂等键包含事件 ID:重复事件不会重复产生副作用
        detail = await _execute_with_event_key(handler, task, event)
        executed.append({"task_id": task["task_id"], "status": detail.get("status")})
    async with sm() as db:
        db.add(AuditEventRow(user_id="system", home_id=auto.home_id, action="automation.triggered",
                             detail={"automation_id": auto.automation_id, "event_id": event.event_id,
                                     "executed": executed}))
        await db.commit()
    logger.info("automation %s triggered by event %s -> %s", auto.automation_id, event.event_id, executed)


async def _execute_with_event_key(handler, task: dict, event: DeviceEvent) -> dict:
    """复用工具处理器,但把事件 ID 编进幂等语义:
    run_set_state 的幂等键由 plan_id+task_id 派生,这里 plan_id 用 event-<id>。"""
    state = {"plan_id": f"event-{event.event_id}", "session_id": f"automation-{event.event_id}",
             "user_id": "system", "home_id": "h1"}
    return await handler(task, state)
