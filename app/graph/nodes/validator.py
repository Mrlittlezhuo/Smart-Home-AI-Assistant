"""计划与策略校验器(§6.6):LLM 与真实操作之间的强制边界。

依次检查:Schema(已在解析时完成)→ 领域/类型注册 → 设备真实存在 →
能力与参数 → 冲突与循环依赖 → 澄清判断。校验失败不执行任何副作用。
"""
from app.device_gateway.adapters.base import DeviceInfo
from app.db.models import PlanRow
from app.db.session import get_sessionmaker
from app.graph.schemas import PlanTask, TaskPlan, TaskTarget
from app.graph.state import AgentState
from app.services import get_services

TASK_TYPES = {"task.get", "task.cancel"}


def _match_device(name: str, devices: list[DeviceInfo]) -> list[DeviceInfo]:
    exact = [d for d in devices if d.name == name or name in d.aliases]
    if exact:
        return exact
    return [d for d in devices if name in d.name or any(name in a for a in d.aliases)]


def resolve_target(
    target: TaskTarget | None, catalog: list[DeviceInfo]
) -> tuple[list[DeviceInfo], str | None]:
    """返回 (匹配设备列表, 澄清问题)。"""
    if target is None:
        return [], "你想操作哪一盏灯或哪个房间的灯?"
    if target.all_home:
        return catalog, None
    if target.device_id:
        dev = [d for d in catalog if d.device_id == target.device_id]
        return (dev, None) if dev else ([], f"没有找到设备 {target.device_id}。")

    pool = catalog
    if target.room_name:
        pool = [d for d in pool if d.room_name == target.room_name]
        if not pool:
            return [], f"没有找到房间「{target.room_name}」里的灯。"
    if target.device_name:
        matched = _match_device(target.device_name, pool)
        if not matched:
            where = f"在{target.room_name}里" if target.room_name else ""
            return [], f"{where}没有找到「{target.device_name}」。"
        if len(matched) > 1:
            names = "、".join(f"{d.room_name}{d.name}" for d in matched)
            return [], f"「{target.device_name}」对应多盏灯:{names},你想控制哪一盏?"
        return matched, None
    # 只给了房间 → 房间灯组
    return pool, None


def _resolve_condition(cond, catalog) -> tuple[list[DeviceInfo], str | None]:
    target = TaskTarget(
        room_name=cond.room_name, device_name=cond.device_name, device_id=cond.device_id
    )
    return resolve_target(target, catalog)


PREF_VALUE_RULES = {
    "default_brightness": {"min": 0, "max": 100},
    "default_color_temp": {"min": 1700, "max": 6500},
}
PREF_KEY_TO_ARG = {"default_brightness": ("brightness_pct", "brightness"),
                   "default_color_temp": ("color_temp_k", "color_temp")}


def _validate_preference_change(pc, catalog: list[DeviceInfo], user_id: str, errors: list[str]) -> dict | None:
    """校验偏好写入:类型、值范围、作用范围真实存在(§6.6 第10条)。"""
    if pc is None:
        return None
    # “删除全部偏好”:未指明任何范围与类型
    if pc.action == "delete" and not pc.preference_key and not pc.device_name and not pc.room_name:
        return {"action": "delete", "scope_type": None, "scope_id": None,
                "scope_name": None, "preference_key": None, "value": None}
    if pc.action == "set":
        if not pc.preference_key:
            errors.append("缺少偏好类型")
            return None
        if pc.value is None:
            errors.append("缺少偏好值")
            return None
        rule = PREF_VALUE_RULES.get(pc.preference_key)
        if rule and not (rule["min"] <= float(pc.value) <= rule["max"]):
            errors.append(f"偏好值 {pc.value} 超出合法范围 [{rule['min']}, {rule['max']}]")
            return None

    scope_type, scope_id, scope_name = "user", user_id, "我的"
    if pc.device_name:
        devs = _match_device(pc.device_name, catalog)
        if len(devs) == 1:
            scope_type, scope_id, scope_name = "device", devs[0].device_id, devs[0].name
        elif len(devs) > 1:
            errors.append(f"「{pc.device_name}」对应多盏灯,请指明哪一盏")
            return None
        else:
            errors.append(f"没有找到「{pc.device_name}」,无法保存该偏好")
            return None
    elif pc.room_name:
        rooms = {d.room_id: d.room_name for d in catalog if d.room_name == pc.room_name}
        if not rooms:
            errors.append(f"没有找到房间「{pc.room_name}」,无法保存该偏好")
            return None
        scope_type, scope_id, scope_name = "room", next(iter(rooms)), pc.room_name

    return {"action": pc.action, "scope_type": scope_type, "scope_id": scope_id,
            "scope_name": scope_name, "preference_key": pc.preference_key, "value": pc.value}


def _find_pref(prefs: list[dict], key: str, scope_type: str, scope_id: str | None) -> dict | None:
    for p in prefs:
        if p["preference_key"] == key and p["scope_type"] == scope_type and (
            scope_type == "user" or p["scope_id"] == scope_id
        ):
            return p
    return None


def _apply_preferences(tasks: list[dict], prefs: list[dict],
                      catalog_by_id: dict[str, DeviceInfo], user_id: str) -> None:
    """用偏好补全用户未说明的参数(§9.3),并记录参数来源。

    优先级:当前明确指令 > 已启用偏好 > 系统默认。
    只在单设备任务上应用设备/房间级偏好;房间灯组任务只应用用户级偏好。
    """
    if not prefs:
        return
    for t in tasks:
        if t["type"] != "light.set_state" or t["arguments"].get("on") is not True:
            continue
        sources: dict[str, str] = {}
        single = len(t["device_ids"]) == 1
        dev = catalog_by_id.get(t["device_ids"][0]) if single else None
        for pref_key, (arg_key, cap_name) in PREF_KEY_TO_ARG.items():
            if arg_key in t["arguments"]:
                continue
            if dev is not None and not any(c.name == cap_name and c.writable for c in dev.capabilities):
                continue
            p = None
            if dev is not None:
                p = _find_pref(prefs, pref_key, "device", dev.device_id) or _find_pref(prefs, pref_key, "room", dev.room_id)
            if p is None:
                p = _find_pref(prefs, pref_key, "user", user_id)
            if p is not None:
                t["arguments"][arg_key] = p["value"]
                sources[arg_key] = f"preference:{p['scope_type']}:{p['scope_name']}"
        if sources:
            t["argument_sources"] = sources


def _detect_confirmation(tasks: list[dict]) -> tuple[bool, str | None]:
    """全屋/批量控制、场景激活与较长时间后的定时任务需要确认(§8.2)。"""
    for t in tasks:
        if t["type"] == "scene.activate":
            return True, f"场景「{t['arguments'].get('scene_name','')}」会执行一组跨领域操作,是否确认?"
        if t.get("all_home") and t.get("device_ids"):
            return True, "这会控制家里全部相关设备,是否确认执行?"
        if t.get("delay_seconds") and t["delay_seconds"] > 3600:
            return True, "这是一个一小时以后的定时任务,是否确认创建?"
    return False, None


def _detect_conflicts(tasks: list[dict]) -> str | None:
    """同一设备在无依赖任务中同时要求开和关 → 冲突澄清(§6.5)。"""
    by_device: dict[str, list[dict]] = {}
    for t in tasks:
        # 延迟任务在时间上分离,不构成同时冲突
        if t.get("delay_seconds"):
            continue
        for dev_id in t.get("device_ids") or []:
            by_device.setdefault(dev_id, []).append(t)
    for dev_id, group in by_device.items():
        on_vals = set()
        for t in group:
            if t["type"] == "light.set_state" and "on" in t["arguments"]:
                independent = all(
                    t["task_id"] not in o.get("depends_on", []) and o["task_id"] not in t["depends_on"]
                    for o in group
                    if o is not t
                )
                if independent:
                    on_vals.add(t["arguments"]["on"])
        if len(on_vals) > 1:
            return "同一盏灯同时被要求打开和关闭,你希望它最终是什么状态?"
    return None


def _has_cycle(tasks: list[dict]) -> bool:
    ids = {t["task_id"] for t in tasks}
    remaining = {t["task_id"]: set(t["depends_on"]) & ids for t in tasks}
    while remaining:
        ready = [k for k, deps in remaining.items() if not deps]
        if not ready:
            return True
        for k in ready:
            remaining.pop(k)
        for deps in remaining.values():
            deps -= set(ready)
    return False


async def validator_node(state: AgentState) -> dict:
    svc = get_services()
    plan = TaskPlan.model_validate(state["candidate_plan"])
    catalog = [DeviceInfo(**d) for d in state["device_catalog"]]

    errors: list[str] = []
    clarifications: list[str] = []
    resolved: list[dict] = []
    pref_change: dict | None = None

    if plan.needs_clarification:
        clarifications.append(plan.clarification_question or "请补充更多信息。")
    else:
        for task in plan.tasks:
            item = {
                "task_id": task.task_id,
                "domain": task.domain,
                "type": task.type,
                "arguments": task.arguments,
                "depends_on": task.depends_on,
                "condition": task.condition.model_dump() if task.condition else None,
                "delay_seconds": task.delay_seconds,
                "device_ids": [],
                "device_names": [],
                "all_home": bool(task.target and task.target.all_home),
            }
            if task.domain == "conversation":
                resolved.append(item)
                continue
            if task.domain == "task":
                if task.type not in TASK_TYPES:
                    errors.append(f"未注册的任务类型 {task.type}")
                resolved.append(item)
                continue
            # 注册表驱动的设备领域校验:新增领域不需要改这里(T-17)
            plugin = svc.domains.get(task.domain)
            if plugin is None:
                errors.append(f"未注册的领域 {task.domain}")
                continue
            if task.type not in plugin.task_types():
                errors.append(f"未注册的任务类型 {task.type}")
                continue
            if plugin.needs_devices:
                pool = [d for d in catalog if d.domain == plugin.name]
                devices, clarify = resolve_target(task.target, pool)
                if clarify:
                    clarifications.append(clarify)
                    continue
                errs = plugin.validate_task(task, devices)
                if errs:
                    # 设备不支持属于明确反馈,不算歧义(§9.4)
                    errors.extend(errs)
                    continue
                item["device_ids"] = [d.device_id for d in devices]
                item["device_names"] = [d.name for d in devices]
                if task.condition is not None:
                    cdevs, cclarify = _resolve_condition(task.condition, pool)
                    if cclarify:
                        clarifications.append(cclarify)
                        continue
                    item["condition"]["device_ids"] = [d.device_id for d in cdevs]
            else:
                errs = plugin.validate_task(task, [])
                if errs:
                    errors.extend(errs)
                    continue
            resolved.append(item)

        pref_change = None
        if plan.preference_change is not None:
            if not state.get("preference_enabled", True):
                # F-19:偏好功能关闭时不加载也不新增
                errors.append("个人偏好功能已关闭,无法修改偏好")
            else:
                pref_change = _validate_preference_change(
                    plan.preference_change, catalog, state["user_id"], errors
                )

        if not errors and not clarifications:
            _apply_preferences(
                resolved, state.get("user_preferences") or [],
                {d.device_id: d for d in catalog}, state["user_id"],
            )
            conflict = _detect_conflicts(resolved)
            if conflict:
                clarifications.append(conflict)
            if _has_cycle(resolved):
                errors.append("任务依赖存在循环")

    needs_confirmation, confirmation_prompt = (False, None)
    if not errors and not clarifications:
        needs_confirmation, confirmation_prompt = _detect_confirmation(resolved)

    ok = not errors and not clarifications and (bool(resolved) or pref_change is not None)
    validation = {
        "ok": ok, "errors": errors, "clarifications": clarifications, "tasks": resolved,
        "needs_confirmation": needs_confirmation, "confirmation_prompt": confirmation_prompt,
        "preference_change": pref_change,
    }

    sm = get_sessionmaker()
    async with sm() as db:
        row = await db.get(PlanRow, state["plan_id"])
        if row is not None:
            row.validation = validation
            row.status = "validated" if ok else "rejected"
            await db.commit()

    return {
        "validation": validation,
        "needs_clarification": bool(clarifications),
        "clarification_question": clarifications[0] if clarifications else state.get("clarification_question"),
    }
