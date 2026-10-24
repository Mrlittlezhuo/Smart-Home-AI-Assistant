"""通用工具处理器:设备 set/get、闲聊、定时任务查询与取消。

设备操作与具体领域无关(能力检查已在校验器完成),窗帘与灯具共用。
副作用处理器带幂等键(§8.3)。
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

import app.services
from app.db.models import ScheduledTaskRow, ToolExecutionRow
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

CHAT_SYSTEM = (
    "你是家庭智能助手“星火”。用简洁、自然的中文回答用户,不要谈论你无法验证的设备操作结果。"
)


def idempotency_key(plan_id: str, task_id: str, device_id: str, arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps([plan_id, task_id, device_id, arguments], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def run_set_state(task: dict, state: dict) -> dict:
    svc = app.services.get_services()
    sm = get_sessionmaker()
    devices = []
    for dev_id in task["device_ids"]:
        key = idempotency_key(state["plan_id"], task["task_id"], dev_id, task["arguments"])
        async with sm() as db:
            existing = (
                await db.execute(
                    select(ToolExecutionRow).where(ToolExecutionRow.idempotency_key == key)
                )
            ).scalars().first()
        if existing is not None and existing.status == "succeeded":
            devices.append({"device_id": dev_id, "status": "verified", "replayed": True,
                            "state": (existing.response or {}).get("state")})
            continue
        res = await svc.gateway.set_state(dev_id, task["arguments"], key)
        async with sm() as db:
            db.add(ToolExecutionRow(
                execution_id=uuid.uuid4().hex, plan_id=state["plan_id"], task_id=task["task_id"],
                tool=task["type"], idempotency_key=key,
                status="succeeded" if res.ok else "failed",
                request={"device_id": dev_id, "desired": task["arguments"]},
                response={"status": res.status, "error": res.error, "state": res.state},
                finished_at=datetime.now(timezone.utc),
            ))
            await db.commit()
        devices.append({"device_id": dev_id, "status": res.status if res.ok else "failed",
                        "error": res.error, "state": res.state})
    ok = all(d["status"] in ("verified", "applied", "accepted") for d in devices)
    return {"status": "succeeded" if ok else "failed", "devices": devices}


async def run_get_state(task: dict, state: dict) -> dict:
    svc = app.services.get_services()
    devices = []
    for dev_id in task["device_ids"]:
        st = await svc.gateway.get_state(dev_id)
        devices.append({"device_id": dev_id, "online": st.online, "state": st.state})
    return {"status": "succeeded", "devices": devices}


async def run_conversation(task: dict, state: dict) -> dict:
    svc = app.services.get_services()
    turns = state.get("recent_turns") or []
    messages = [
        {"role": "user" if t["role"] == "user" else "assistant", "content": t["content"]}
        for t in turns[-6:]
    ]
    if not messages or messages[-1]["content"] != state["current_input"]:
        messages.append({"role": "user", "content": state["current_input"]})
    try:
        text = await svc.llm.complete(CHAT_SYSTEM, messages, max_tokens=800)
        return {"status": "succeeded", "text": text.strip()}
    except Exception as e:  # noqa: BLE001
        logger.warning("conversation failed: %s", e)
        return {"status": "failed", "error": "chat_unavailable"}


async def run_task_get(task: dict, state: dict) -> dict:
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(ScheduledTaskRow)
                .where(ScheduledTaskRow.session_id == state["session_id"],
                       ScheduledTaskRow.status == "pending")
                .order_by(ScheduledTaskRow.created_at.desc())
            )
        ).scalars().all()
        return {"status": "succeeded", "tasks": [
            {"task_id": r.task_id, "run_at": r.run_at.isoformat(),
             "arguments": r.payload.get("arguments"), "device_names": r.payload.get("device_names")}
            for r in rows]}


async def run_task_cancel(task: dict, state: dict) -> dict:
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(ScheduledTaskRow)
                .where(ScheduledTaskRow.session_id == state["session_id"],
                       ScheduledTaskRow.status == "pending")
                .order_by(ScheduledTaskRow.created_at.desc())
            )
        ).scalars().all()
        if rows:
            rows[0].status = "cancelled"
            await db.commit()
            return {"status": "succeeded", "cancelled_task_id": rows[0].task_id,
                    "device_names": rows[0].payload.get("device_names"),
                    "arguments": rows[0].payload.get("arguments")}
        return {"status": "failed", "error": "没有找到待执行的任务"}
