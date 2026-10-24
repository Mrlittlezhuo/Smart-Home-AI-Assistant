"""重启恢复(T-15):确认中断与定时任务都持久在 Postgres,进程重启后可继续。"""
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.config import settings
from app.db.models import ScheduledTaskRow
from app.db.session import get_sessionmaker
from app.graph.main_graph import build_graph
from app.worker.scheduler import claim_due_tasks, execute_task
from tests.e2e.test_confirmation import ALL_DEVICES, ALL_HOME_PLAN


async def test_interrupt_resumes_after_process_restart(services):
    """图实例与检查点连接在确认中断后彻底关闭(模拟进程退出),
    新连接 + 新图实例仍能用 Command(resume) 从原计划恢复执行。"""
    _, adapter = services
    for dev in ALL_DEVICES:
        await adapter.set_state(dev, {"on": True}, f"restart-{dev}")

    thread = "s-restart-1"
    config = {"configurable": {"thread_id": thread}}

    cm1 = AsyncPostgresSaver.from_conn_string(settings.checkpoint_pg_dsn)
    saver1 = await cm1.__aenter__()
    await saver1.setup()
    try:
        first = await build_graph(checkpointer=saver1).ainvoke(
            {"session_id": thread, "current_input": "关闭家里所有灯", "fixed_plan": ALL_HOME_PLAN},
            config=config)
        assert first.get("__interrupt__"), "全屋控制应先中断等待确认"
    finally:
        await cm1.__aexit__(None, None, None)  # 关闭连接池:检查点只存在于 Postgres

    cm2 = AsyncPostgresSaver.from_conn_string(settings.checkpoint_pg_dsn)
    saver2 = await cm2.__aenter__()
    try:
        final = await build_graph(checkpointer=saver2).ainvoke(
            Command(resume={"confirmed": True}), config=config)
    finally:
        await cm2.__aexit__(None, None, None)

    assert "已关闭" in final["response"]
    for dev in ALL_DEVICES:
        assert (await adapter.get_state(dev)).state["on"] is False


async def test_pending_scheduled_task_picked_up_after_worker_restart(services):
    """定时任务已落库且到期:Worker 重启后从任务表领取并执行,不丢任务。"""
    _, adapter = services
    async with get_sessionmaker()() as db:
        db.add(ScheduledTaskRow(
            task_id="sched-restart", session_id="s-restart-2", user_id="u1", home_id="h1",
            payload={"task_id": "t1", "type": "light.set_state", "arguments": {"on": True},
                     "device_ids": ["dev-bedside"], "device_names": ["床头灯"]},
            run_at=datetime.now(timezone.utc) - timedelta(seconds=5), status="pending",
        ))
        await db.commit()

    claimed = await claim_due_tasks()
    assert any(r.task_id == "sched-restart" for r in claimed)
    for row in claimed:
        await execute_task(row)

    assert (await adapter.get_state("dev-bedside")).state["on"] is True
    # 已完成的任务不会被再次领取
    assert not await claim_due_tasks()
