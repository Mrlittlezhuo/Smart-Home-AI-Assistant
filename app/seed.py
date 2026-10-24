"""内置数据种子:跨领域场景与示例自动化(按名称幂等 upsert)。

“离家模式”用于验证 T-18(跨领域统一规划/确认/执行/汇总);
“走廊有人自动开灯”类自动化用于验证 T-19(事件去重、状态更新、按启用规则触发)。
"""
from sqlalchemy import select

from app.db.models import AutomationRow, ScenesRow
from app.db.session import get_sessionmaker

BUILTIN_SCENES = [
    {
        "scene_id": "scene-away",
        "home_id": "h1",
        "name": "离家模式",
        "actions": [
            {"domain": "lighting", "type": "light.set_state",
             "target": {"all_home": True}, "arguments": {"on": False}},
            {"domain": "curtain", "type": "curtain.set_position",
             "target": {"all_home": True}, "arguments": {"position_pct": 0}},
        ],
        "builtin": True,
    },
]

BUILTIN_AUTOMATIONS = [
    {
        "automation_id": "auto-living-motion",
        "home_id": "h1",
        "name": "客厅有人且灯未开时自动开灯",
        "enabled": True,
        "trigger": {"event_type": "motion_detected", "device_id": "dev-living-motion"},
        "preconditions": [{"device_id": "dev-living-main", "field": "on", "equals": False}],
        "actions": [{"type": "light.set_state", "arguments": {"on": True},
                     "device_ids": ["dev-living-main"], "device_names": ["客厅主灯"]}],
    },
]


async def seed_builtin(home_id: str = "h1") -> None:
    sm = get_sessionmaker()
    async with sm() as db:
        for spec in BUILTIN_SCENES:
            existing = await db.get(ScenesRow, spec["scene_id"])
            if existing is None:
                db.add(ScenesRow(**spec))
        for spec in BUILTIN_AUTOMATIONS:
            existing = await db.get(AutomationRow, spec["automation_id"])
            if existing is None:
                db.add(AutomationRow(**spec))
        await db.commit()
