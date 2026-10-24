"""对话、确认与偏好接口(§11)。API 不理解意图、不直接调用 LLM、不访问设备协议。"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.db.models import AuditEventRow, MessageRow, PlanRow, PreferenceRow, UserSettingRow
from app.db.session import get_sessionmaker
from app.services import get_services

router = APIRouter(prefix="/v1")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    user_id: str | None = None
    use_preferences: bool = True
    # 阶段一/测试:跳过 LLM 的固定候选计划
    fixed_plan: dict | None = None


class ChatResponse(BaseModel):
    reply: str
    plan_id: str | None = None
    plan_hash: str | None = None
    needs_clarification: bool = False
    awaiting_confirmation: bool = False
    results: list[dict] = []


def _thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    svc = get_services()
    final = await svc.graph.ainvoke(
        {
            "session_id": req.session_id,
            "current_input": req.message,
            "fixed_plan": req.fixed_plan,
            "user_id": req.user_id or settings.default_user_id,
            "home_id": settings.default_home_id,
            "timezone": settings.default_timezone,
            "use_preferences": req.use_preferences,
        },
        config=_thread_config(req.session_id),
    )
    interrupts = final.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value or {}
        return ChatResponse(
            reply=payload.get("prompt") or "该操作需要你的确认。",
            plan_id=payload.get("plan_id"),
            plan_hash=payload.get("plan_hash"),
            awaiting_confirmation=True,
        )
    return ChatResponse(
        reply=final.get("response") or "",
        plan_id=final.get("plan_id"),
        needs_clarification=bool(final.get("needs_clarification")),
        results=final.get("execution_results") or [],
    )


class ConfirmRequest(BaseModel):
    plan_hash: str = Field(min_length=1)
    confirmed: bool = True


@router.post("/plans/{plan_id}/confirm", response_model=ChatResponse)
async def confirm_plan(plan_id: str, req: ConfirmRequest) -> ChatResponse:
    """确认/取消待确认计划(§11.2):哈希必须匹配,恢复同一线程的同一计划。"""
    svc = get_services()
    sm = get_sessionmaker()
    async with sm() as db:
        row = await db.get(PlanRow, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    if row.plan_hash != req.plan_hash:
        raise HTTPException(status_code=409, detail="计划内容已变化,请重新发起")

    final = await svc.graph.ainvoke(
        Command(resume={"confirmed": req.confirmed}),
        config=_thread_config(row.session_id),
    )
    return ChatResponse(
        reply=final.get("response") or "",
        plan_id=plan_id,
        results=final.get("execution_results") or [],
    )


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, limit: int = 100) -> dict:
    """聊天记录模式(阶段零):默认以当前会话为范围返回历史消息。"""
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(MessageRow).where(MessageRow.session_id == session_id)
                .order_by(MessageRow.id.desc()).limit(min(limit, 200))
            )
        ).scalars().all()
    return {"messages": [
        {"role": r.role, "content": r.content, "created_at": r.created_at.isoformat()}
        for r in reversed(rows)
    ]}


class FailureRequest(BaseModel):
    device_id: str
    # offline | timeout | reject | null(恢复)
    mode: str | None = None


class DeviceEventRequest(BaseModel):
    device_id: str
    event_type: str
    payload: dict = Field(default_factory=dict)
    event_id: str | None = None


@router.post("/debug/device-event")
async def inject_device_event(req: DeviceEventRequest) -> dict:
    """开发/演示用:模拟设备主动上报事件,走完整事件管道(真实部署应关闭)。"""
    svc = get_services()
    adapter = svc.gateway.adapter
    if hasattr(adapter, "inject_event"):
        await adapter.inject_event(req.device_id, req.event_type, req.payload, req.event_id)
        return {"ok": True, "event_id": req.event_id}
    return {"ok": False, "error": "adapter does not support event injection"}


@router.post("/debug/device-failure")
async def inject_device_failure(req: FailureRequest) -> dict:
    """开发/演示用:向 Mock 适配器注入故障(真实适配器部署应关闭此路由)。"""
    svc = get_services()
    adapter = svc.gateway.adapter
    if hasattr(adapter, "set_failure_mode"):
        adapter.set_failure_mode(req.device_id, req.mode)
        return {"ok": True, "device_id": req.device_id, "mode": req.mode}
    return {"ok": False, "error": "adapter does not support failure injection"}


# ---- 偏好管理接口(§11.3)----

class PreferenceUser(BaseModel):
    user_id: str | None = None


class PreferenceUpdate(BaseModel):
    value: float | int | str


class PreferenceSettings(BaseModel):
    preference_enabled: bool


def _uid(user_id: str | None) -> str:
    return user_id or settings.default_user_id


@router.get("/preferences")
async def list_preferences(user_id: str | None = None) -> list[dict]:
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(PreferenceRow).where(PreferenceRow.user_id == _uid(user_id),
                                            PreferenceRow.enabled.is_(True))
            )
        ).scalars().all()
    return [
        {"preference_id": r.preference_id, "scope_type": r.scope_type, "scope_name": r.scope_name,
         "preference_key": r.preference_key, "value": r.value, "source": r.source}
        for r in rows
    ]


@router.put("/preferences/settings")
async def update_preference_settings(req: PreferenceSettings, user_id: str | None = None) -> dict:
    uid = _uid(user_id)
    sm = get_sessionmaker()
    async with sm() as db:
        row = await db.get(UserSettingRow, uid)
        if row is None:
            db.add(UserSettingRow(user_id=uid, preference_enabled=req.preference_enabled))
        else:
            row.preference_enabled = req.preference_enabled
        db.add(AuditEventRow(user_id=uid, home_id=settings.default_home_id,
                             action="preference.settings",
                             detail={"preference_enabled": req.preference_enabled}))
        await db.commit()
    return {"ok": True, "preference_enabled": req.preference_enabled}


@router.put("/preferences/{preference_id}")
async def update_preference(preference_id: str, req: PreferenceUpdate) -> dict:
    sm = get_sessionmaker()
    async with sm() as db:
        row = await db.get(PreferenceRow, preference_id)
        if row is None or not row.enabled:
            raise HTTPException(status_code=404, detail="preference not found")
        row.value = req.value
        row.source = "settings"
        row.updated_at = datetime.now(timezone.utc)
        db.add(AuditEventRow(user_id=row.user_id, home_id=row.home_id, action="preference.update",
                             detail={"preference_id": preference_id, "value": req.value}))
        await db.commit()
    return {"ok": True}


@router.delete("/preferences/{preference_id}")
async def delete_preference(preference_id: str) -> dict:
    sm = get_sessionmaker()
    async with sm() as db:
        row = await db.get(PreferenceRow, preference_id)
        if row is None or not row.enabled:
            raise HTTPException(status_code=404, detail="preference not found")
        row.enabled = False
        db.add(AuditEventRow(user_id=row.user_id, home_id=row.home_id, action="preference.delete",
                             detail={"preference_id": preference_id}))
        await db.commit()
    return {"ok": True}


@router.delete("/preferences")
async def delete_all_preferences(user_id: str | None = None) -> dict:
    uid = _uid(user_id)
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(PreferenceRow).where(PreferenceRow.user_id == uid,
                                            PreferenceRow.enabled.is_(True))
            )
        ).scalars().all()
        for r in rows:
            r.enabled = False
        db.add(AuditEventRow(user_id=uid, home_id=settings.default_home_id,
                             action="preference.delete_all", detail={"count": len(rows)}))
        await db.commit()
    return {"ok": True, "count": len(rows)}
