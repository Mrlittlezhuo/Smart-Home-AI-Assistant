"""任务执行器(§6.7):确定性节点,只接收已校验计划。

- 按任务类型从工具注册中心取处理器,不认识具体领域(T-17);
- 无依赖且设备不冲突的任务并行;同一设备的任务默认串行(按计划顺序插入隐式依赖);
- 前置失败 → 依赖它的任务跳过;无关分支继续;
- 条件用实时状态查询评估;延迟任务写入 scheduled_tasks,由 Worker 到期执行。
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import app.services
from app.db.models import PlanTaskRow, ScheduledTaskRow
from app.db.session import get_sessionmaker
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

TASK_CONTROL_TYPES = {"task.get", "task.cancel"}


def _topological_waves(tasks: list[dict]) -> list[list[dict]]:
    """按依赖分层;操作同一设备的任务按计划顺序隐式串行。"""
    ids = {t["task_id"] for t in tasks}
    for i, t in enumerate(tasks):
        for prev in tasks[:i]:
            shared = set(t.get("device_ids") or []) & set(prev.get("device_ids") or [])
            if shared and prev["task_id"] not in t["depends_on"]:
                t["depends_on"] = t["depends_on"] + [prev["task_id"]]

    waves: list[list[dict]] = []
    done: set[str] = set()
    remaining = list(tasks)
    while remaining:
        wave = [t for t in remaining if all(d in done for d in t["depends_on"] if d in ids)]
        if not wave:
            wave = [remaining[0]]  # 防御:校验器已排除循环依赖
        waves.append(wave)
        for t in wave:
            remaining.remove(t)
            done.add(t["task_id"])
    return waves


async def _eval_condition(cond: dict) -> bool:
    svc = app.services.get_services()
    field, expected = cond["field"], cond["equals"]
    for dev_id in cond.get("device_ids") or []:
        st = await svc.gateway.get_state(dev_id)
        if field == "online":
            actual = st.online
        elif field == "on":
            actual = st.state.get("on")
        else:
            actual = st.state.get(field)
        if actual != expected:
            return False
    return True


async def _run_scheduled(task: dict, state: AgentState) -> dict:
    run_at = datetime.now(timezone.utc) + timedelta(seconds=task["delay_seconds"])
    sm = get_sessionmaker()
    async with sm() as db:
        db.add(ScheduledTaskRow(
            task_id=uuid_hex(), session_id=state["session_id"],
            user_id=state["user_id"], home_id=state["home_id"],
            payload={k: task[k] for k in ("task_id", "type", "arguments", "device_ids", "device_names")},
            run_at=run_at, status="pending",
        ))
        await db.commit()
    return {"status": "scheduled", "run_at": run_at.isoformat(), "delay_seconds": task["delay_seconds"]}


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex


async def _run_task(task: dict, state: AgentState, skipped_reason: str | None) -> dict:
    base = {"task_id": task["task_id"], "type": task["type"],
            "device_names": task.get("device_names") or [], "arguments": task["arguments"]}
    if task.get("argument_sources"):
        base["argument_sources"] = task["argument_sources"]
    if skipped_reason:
        return {**base, "status": "skipped", "error": skipped_reason}

    if task["condition"] is not None and not await _eval_condition(task["condition"]):
        return {**base, "status": "skipped", "error": "condition_false"}

    if task.get("delay_seconds"):
        detail = await _run_scheduled(task, state)
    else:
        handler = app.services.get_services().tools.handler_for(task["type"])
        if handler is None:
            detail = {"status": "failed", "error": f"unregistered tool {task['type']}"}
        else:
            detail = await handler(task, state)

    return {**base, **detail}


async def executor_node(state: AgentState) -> dict:
    tasks = state["validation"]["tasks"]
    results: list[dict] = []

    pc = state["validation"].get("preference_change")
    if pc is not None:
        from app.graph.nodes.preferences import _run_preference_change
        pref_result = await _run_preference_change(pc, state)
        results.append(pref_result)
        if not tasks:
            return {"execution_results": results}

    waves = _topological_waves(tasks)
    status_by_task: dict[str, str] = {}

    sm = get_sessionmaker()
    async with sm() as db:
        for t in tasks:
            db.add(PlanTaskRow(
                plan_id=state["plan_id"], task_id=t["task_id"], domain=t["domain"],
                type=t["type"], device_id=(t["device_ids"] or [None])[0],
                arguments=t["arguments"], depends_on=t["depends_on"], condition=t["condition"],
            ))
        await db.commit()

    for wave in waves:
        outcomes = []
        for t in wave:
            failed_dep = next(
                (d for d in t["depends_on"] if status_by_task.get(d) in ("failed", "skipped")), None
            )
            reason = f"dependency_failed:{failed_dep}" if failed_dep else None
            outcomes.append(_run_task(t, state, reason))
        gathered = await asyncio.gather(*outcomes)
        for t, out in zip(wave, gathered):
            status_by_task[t["task_id"]] = out["status"]
            results.append(out)
            async with sm() as db:
                row = (
                    await db.execute(
                        select(PlanTaskRow).where(
                            PlanTaskRow.plan_id == state["plan_id"],
                            PlanTaskRow.task_id == t["task_id"],
                        )
                    )
                ).scalars().first()
                if row is not None:
                    row.status = out["status"]
                    row.error = out.get("error")
                    row.result = {k: v for k, v in out.items() if k not in ("task_id", "type")}
                    await db.commit()

    # 最近操作对象(供下一轮指代解析)
    targets: list[dict] = []
    for r in results:
        for d in r.get("devices") or []:
            name = next((n for n, did in zip(r.get("device_names", []),
                                             [x["device_id"] for x in r["devices"]]) if did == d["device_id"]),
                        d["device_id"])
            targets.append({"device_id": d["device_id"], "name": name})
    if not targets:
        targets = state.get("recent_targets") or []

    return {"execution_results": results, "recent_targets": targets}
