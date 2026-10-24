"""上下文构建节点(§6.4):生成 ContextPackage 的原料,不拼接提示词。"""
from sqlalchemy import select

from app.config import settings
from app.db.models import MessageRow, PreferenceRow, ScenesRow, SessionRow, UserSettingRow
from app.db.session import get_sessionmaker
from app.graph.state import AgentState
from app.services import get_services


async def context_node(state: AgentState) -> dict:
    svc = get_services()
    home_id = state.get("home_id") or settings.default_home_id
    user_id = state.get("user_id") or settings.default_user_id
    session_id = state["session_id"]

    use_preferences = state.get("use_preferences", True)
    sm = get_sessionmaker()
    async with sm() as db:
        session = await db.get(SessionRow, session_id)
        if session is None:
            db.add(SessionRow(session_id=session_id, user_id=user_id, home_id=home_id))
        rows = (
            await db.execute(
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .order_by(MessageRow.id.desc())
                .limit(settings.context_recent_turns * 2)
            )
        ).scalars().all()
        recent_turns = [{"role": r.role, "content": r.content} for r in reversed(rows)]
        db.add(MessageRow(session_id=session_id, role="user", content=state["current_input"]))

        # 偏好加载:总开关关闭或本次请求不使用时不注入(§9.4 / F-19)
        user_prefs: list[dict] = []
        preference_enabled = True
        setting = await db.get(UserSettingRow, user_id)
        if setting is None:
            db.add(UserSettingRow(user_id=user_id, preference_enabled=True))
        else:
            preference_enabled = setting.preference_enabled
        if use_preferences and preference_enabled:
                prefs = (
                    await db.execute(
                        select(PreferenceRow).where(
                            PreferenceRow.user_id == user_id, PreferenceRow.enabled.is_(True)
                        )
                    )
                ).scalars().all()
                user_prefs = [
                    {"preference_id": p.preference_id, "scope_type": p.scope_type,
                     "scope_id": p.scope_id, "scope_name": p.scope_name,
                     "preference_key": p.preference_key, "value": p.value}
                    for p in prefs
                ]
        await db.commit()

    async with sm() as db:
        scenes = (
            await db.execute(select(ScenesRow).where(ScenesRow.home_id == home_id))
        ).scalars().all()
        scene_names = [s.name for s in scenes]

    catalog = await svc.gateway.list_devices(home_id)
    # 检查点会恢复上一轮线程状态:单轮字段必须在此重置,
    # 否则会复用旧的 response/validation(跨轮状态污染)。
    return {
        "user_id": user_id,
        "home_id": home_id,
        "timezone": state.get("timezone") or settings.default_timezone,
        "recent_turns": recent_turns,
        "device_catalog": [d.model_dump() for d in catalog],
        "scenes": scene_names,
        "use_preferences": use_preferences,
        "preference_enabled": preference_enabled,
        "user_preferences": user_prefs,
        "approval_state": None,
        "candidate_plan": None,
        "plan_id": "",
        "plan_hash": "",
        "validation": None,
        "execution_results": [],
        "needs_clarification": False,
        "clarification_question": None,
        "response": None,
    }
