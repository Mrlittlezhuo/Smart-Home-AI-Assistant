"""延迟/定时任务 Worker(§12)。

PostgreSQL 任务表 + 行锁领取;到期后重新检查权限与设备,调用工具并记录结果。
运行:python -m app.worker.scheduler
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.automations.engine import handle_event
from app.db.models import ScheduledTaskRow
from app.db.session import get_sessionmaker, init_db
from app.device_gateway.factory import build_adapter
from app.device_gateway.gateway import DeviceGateway
from app.llm.gateway import LLMGateway
from app.seed import seed_builtin
from app.services import Services, get_services, set_services

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 5.0


async def claim_due_tasks() -> list[ScheduledTaskRow]:
    now = datetime.now(timezone.utc)
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(ScheduledTaskRow)
                .where(ScheduledTaskRow.status == "pending", ScheduledTaskRow.run_at <= now)
                .with_for_update(skip_locked=True)
                .limit(10)
            )
        ).scalars().all()
        for r in rows:
            r.status = "running"
            r.attempts += 1
        await db.commit()
        return list(rows)


async def execute_task(row: ScheduledTaskRow) -> None:
    """到期执行:重新检查(任务未被取消),经工具处理器执行(自带幂等与落库)。"""
    svc = get_services()
    payload = row.payload
    handler = svc.tools.handler_for(payload["type"])
    if handler is None:
        ok = False
    else:
        task = {"task_id": payload.get("task_id", ""), "type": payload["type"],
                "arguments": payload["arguments"],
                "device_ids": payload.get("device_ids") or [],
                "device_names": payload.get("device_names") or []}
        state = {"plan_id": f"sched-{row.task_id}", "session_id": row.session_id,
                 "user_id": row.user_id, "home_id": row.home_id}
        detail = await handler(task, state)
        ok = detail.get("status") == "succeeded"
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ScheduledTaskRow).where(ScheduledTaskRow.task_id == row.task_id)
            .values(status="done" if ok else "failed")
        )
        await db.commit()
    logger.info("scheduled task %s -> %s", row.task_id, "done" if ok else "failed")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()
    svc = Services(gateway=DeviceGateway(build_adapter()), llm=LLMGateway())
    set_services(svc)
    svc.gateway.adapter.subscribe_events(handle_event)
    await seed_builtin()
    logger.info("worker started, polling every %.1fs", POLL_INTERVAL_S)
    while True:
        try:
            due = await claim_due_tasks()
            for row in due:
                await execute_task(row)
        except Exception:  # noqa: BLE001
            logger.exception("worker loop error")
        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
