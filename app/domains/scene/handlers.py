"""scene.activate:按场景定义展开跨领域动作并逐个执行。"""
import logging

from sqlalchemy import select

import app.services
from app.db.models import ScenesRow
from app.db.session import get_sessionmaker
from app.device_gateway.adapters.base import DeviceInfo
from app.graph.schemas import TaskTarget
from app.graph.nodes.validator import resolve_target

logger = logging.getLogger(__name__)


async def run_scene_activate(task: dict, state: dict) -> dict:
    svc = app.services.get_services()
    scene_name = task["arguments"]["scene_name"]
    sm = get_sessionmaker()
    async with sm() as db:
        scene = (
            await db.execute(
                select(ScenesRow).where(ScenesRow.home_id == state["home_id"],
                                        ScenesRow.name == scene_name)
            )
        ).scalars().first()
    if scene is None:
        return {"status": "failed", "error": f"场景「{scene_name}」不存在"}

    catalog = [DeviceInfo(**d) for d in state["device_catalog"]]
    actions_out = []
    all_ok = True
    for i, action in enumerate(scene.actions):
        pool = [d for d in catalog if d.domain == action["domain"]]
        target = TaskTarget(**(action.get("target") or {}))
        devices, clarify = resolve_target(target, pool)
        if clarify or not devices:
            actions_out.append({"action": action, "status": "failed", "error": clarify or "没有匹配设备"})
            all_ok = False
            continue
        sub = {
            "task_id": f"{task['task_id']}:a{i}",
            "type": action["type"],
            "arguments": action["arguments"],
            "device_ids": [d.device_id for d in devices],
            "device_names": [d.name for d in devices],
        }
        handler = svc.tools.handler_for(action["type"])
        if handler is None:
            actions_out.append({"action": action, "status": "failed", "error": f"未注册动作 {action['type']}"})
            all_ok = False
            continue
        detail = await handler(sub, state)
        actions_out.append({
            "action": action, "status": detail["status"],
            "devices": detail.get("devices") or [], "device_names": sub["device_names"],
        })
        if detail["status"] not in ("succeeded", "verified"):
            all_ok = False

    return {"status": "succeeded" if all_ok else "failed",
            "scene": scene_name, "actions": actions_out}


HANDLERS = {"scene.activate": run_scene_activate}
