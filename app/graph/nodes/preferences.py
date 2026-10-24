"""偏好工具执行(§6.8 / §9.2):写入必须已通过校验,来源记 explicit_chat。"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import AuditEventRow, PreferenceRow
from app.db.session import get_sessionmaker
from app.graph.state import AgentState


async def _run_preference_change(pc: dict, state: AgentState) -> dict:
    sm = get_sessionmaker()
    async with sm() as db:
        if pc["action"] == "set":
            existing = (
                await db.execute(
                    select(PreferenceRow).where(
                        PreferenceRow.user_id == state["user_id"],
                        PreferenceRow.scope_type == pc["scope_type"],
                        PreferenceRow.scope_id == pc["scope_id"],
                        PreferenceRow.preference_key == pc["preference_key"],
                    )
                )
            ).scalars().first()
            if existing is not None:
                existing.value = pc["value"]
                existing.enabled = True
                existing.source = "explicit_chat"
                existing.updated_at = datetime.now(timezone.utc)
                pref_id = existing.preference_id
            else:
                pref_id = uuid.uuid4().hex
                db.add(PreferenceRow(
                    preference_id=pref_id, user_id=state["user_id"], home_id=state["home_id"],
                    scope_type=pc["scope_type"], scope_id=pc["scope_id"], scope_name=pc["scope_name"],
                    preference_key=pc["preference_key"], value=pc["value"], source="explicit_chat",
                ))
            db.add(AuditEventRow(user_id=state["user_id"], home_id=state["home_id"],
                                 action="preference.set",
                                 detail={"preference_id": pref_id, **pc}))
            await db.commit()
            return {"status": "succeeded", "type": "preference.set",
                    "preference": {k: pc[k] for k in ("scope_type", "scope_name", "preference_key", "value")}}

        if pc["action"] == "delete":
            q = select(PreferenceRow).where(PreferenceRow.user_id == state["user_id"],
                                            PreferenceRow.enabled.is_(True))
            if pc.get("preference_key"):
                q = q.where(PreferenceRow.preference_key == pc["preference_key"])
            if pc.get("scope_type"):
                q = q.where(PreferenceRow.scope_type == pc["scope_type"],
                            PreferenceRow.scope_id == pc["scope_id"])
            rows = (await db.execute(q)).scalars().all()
            for r in rows:
                r.enabled = False
            db.add(AuditEventRow(user_id=state["user_id"], home_id=state["home_id"],
                                 action="preference.delete", detail={"count": len(rows), **pc}))
            await db.commit()
            return {"status": "succeeded", "type": "preference.delete", "count": len(rows)}

        rows = (
            await db.execute(
                select(PreferenceRow).where(PreferenceRow.user_id == state["user_id"],
                                            PreferenceRow.enabled.is_(True))
            )
        ).scalars().all()
        return {"status": "succeeded", "type": "preference.list", "preferences": [
            {"scope_type": r.scope_type, "scope_name": r.scope_name,
             "preference_key": r.preference_key, "value": r.value}
            for r in rows]}
