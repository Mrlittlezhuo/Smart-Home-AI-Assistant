"""结果回复节点(§3.6 / §9):程序先汇总真实结果,模板组织语言。

回复只能基于执行事实,不虚构成功状态。
"""
from datetime import datetime, timezone

from app.db.models import MessageRow
from app.db.session import get_sessionmaker
from app.graph.state import AgentState

_DELAY_WORDS = [(3600, "小时"), (60, "分钟")]


def _delay_text(seconds: int) -> str:
    for unit_seconds, unit in _DELAY_WORDS:
        if seconds % unit_seconds == 0 and seconds >= unit_seconds:
            return f"{seconds // unit_seconds}{unit}"
    return f"{seconds}秒"


def _action_phrase(arguments: dict, name: str) -> str:
    parts = []
    if "on" in arguments:
        parts.append(f"{'打开' if arguments['on'] else '关闭'}{name}")
    if "brightness_pct" in arguments:
        parts.append(f"将{name}亮度调到{arguments['brightness_pct']}%")
    if "color_temp_k" in arguments:
        parts.append(f"将{name}色温调到{arguments['color_temp_k']}K")
    if "color_hex" in arguments:
        parts.append(f"将{name}颜色调为{arguments['color_hex']}")
    if "position_pct" in arguments:
        v = arguments["position_pct"]
        if v == 0:
            parts.append(f"关闭{name}")
        elif v == 100:
            parts.append(f"完全打开{name}")
        else:
            parts.append(f"将{name}打开到{v}%")
    return "、".join(parts) if parts else f"操作{name}"


def _error_text(err: str | None) -> str:
    return {
        "device_offline": "当前离线",
        "device_timeout": "响应超时",
        "device_rejected": "拒绝执行",
    }.get(err or "", err or "执行失败")


def _state_phrase(dev: dict, name: str) -> str:
    if not dev["online"]:
        return f"{name}当前离线"
    st = dev["state"]
    if "position_pct" in st and "on" not in st:
        return f"{name}当前开合{st['position_pct']}%"
    if not st.get("on"):
        return f"{name}处于关闭状态"
    bits = [f"{name}开着"]
    if "brightness_pct" in st:
        bits.append(f"亮度{st['brightness_pct']}%")
    if "color_temp_k" in st:
        bits.append(f"色温{st['color_temp_k']}K")
    return ",".join(bits)


_PREF_KEY_LABELS = {
    "default_brightness": lambda v: f"默认亮度{v}%",
    "default_color_temp": lambda v: f"默认色温{v}K",
    "alias": lambda v: f"别名{v}",
}


def _pref_desc(p: dict) -> str:
    label = _PREF_KEY_LABELS.get(p["preference_key"], lambda v: f"{p['preference_key']}={v}")
    scope = p.get("scope_name") or "全局"
    return f"{scope}:{label(p['value'])}"


def summarize(results: list[dict]) -> str:
    success: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for r in results:
        status = r["status"]
        names = r.get("device_names") or []
        if r["type"] == "preference.set":
            success.append(f"已记住:{_pref_desc(r['preference'])}。你可以说“你记住了我什么”查看或删除")
            continue
        if r["type"] == "preference.delete":
            success.append(f"已删除{r['count']}项偏好。" if r.get("count") else "没有找到相关偏好。")
            continue
        if r["type"] == "preference.list":
            prefs = r.get("preferences") or []
            success.append("我记住了:" + "、".join(_pref_desc(p) for p in prefs) + "。"
                           if prefs else "目前没有记住任何偏好。")
            continue
        if r["type"] == "conversation.reply":
            if status == "succeeded":
                success.append(r.get("text", ""))
            else:
                failed.append("闲聊回复生成失败")
            continue
        if r["type"] == "task.get":
            tasks = r.get("tasks") or []
            if not tasks:
                success.append("当前没有待执行的任务")
            else:
                desc = ";".join(
                    f"{_delay_text(max(0, round((datetime.fromisoformat(t['run_at']) - datetime.now(timezone.utc)).total_seconds())))}后"
                    f"{_action_phrase(t.get('arguments') or {}, '、'.join(t.get('device_names') or []))}"
                    for t in tasks
                )
                success.append(f"待执行任务:{desc}")
            continue
        if r["type"] == "task.cancel":
            if status == "succeeded":
                names = r.get("device_names") or []
                success.append(f"已取消对{'、'.join(names) or '该任务'}的定时操作")
            else:
                failed.append(_error_text(r.get("error")))
            continue
        if status == "scheduled":
            action = _action_phrase(r["arguments"], "、".join(names))
            success.append(f"好的,我会在{_delay_text(r['delay_seconds'])}后{action}")
            continue
        if r["type"] == "scene.activate":
            if status == "succeeded":
                seg = []
                for a in r.get("actions") or []:
                    seg.append(_action_phrase(a["action"]["arguments"], "、".join(a.get("device_names") or [])))
                success.append(f"已执行{r['scene']}:" + "、".join(seg))
            else:
                errs = ([r["error"]] if r.get("error") else []) + [
                    a.get("error") for a in r.get("actions") or [] if a.get("error")]
                failed.append(f"{r.get('scene','场景')}执行不完整" + (":" + ";".join(errs) if errs else ""))
            continue
        devices = r.get("devices") or []
        if status == "succeeded":
            if r["type"].endswith(".get_state"):
                id2name = dict(zip([d["device_id"] for d in devices], names))
                success.extend(_state_phrase(d, id2name.get(d["device_id"], d["device_id"])) for d in devices)
            else:
                id2name = dict(zip([d["device_id"] for d in devices], names))
                phrase = "已" + _action_phrase(r["arguments"], "、".join(names or list(id2name.values())))
                if r.get("argument_sources"):
                    phrase += "(按你保存的默认偏好)"
                success.append(phrase)
        elif status == "skipped":
            reason = r.get("error") or ""
            if reason == "condition_false":
                action = _action_phrase(r["arguments"], "")
                skipped.append(f"{'、'.join(names)}的条件不满足,未{action}")
            else:
                skipped.append(f"因前置步骤未成功,跳过{'、'.join(names)}")
        else:
            errs = {_error_text(d.get("error")) for d in devices if d.get("error")}
            failed.append(f"{'、'.join(names)}{_error_text(r.get('error')) if not errs else '、'.join(sorted(errs))}")

    if not results:
        return "没有可执行的任务。"
    segments = [s.rstrip("。") for s in (success + skipped + failed) if s]
    return "。".join(segments) + "。" if segments else "没有可执行的任务。"


async def responder_node(state: AgentState) -> dict:
    if state.get("response"):
        reply = state["response"]
    elif state.get("needs_clarification"):
        reply = state.get("clarification_question") or "请补充更多信息。"
    elif state.get("validation") and not state["validation"]["ok"]:
        errors = state["validation"]["errors"]
        reply = "未能执行:" + ";".join(errors) + "。" if errors else "未能执行该请求。"
    else:
        reply = summarize(state.get("execution_results") or [])

    sm = get_sessionmaker()
    async with sm() as db:
        db.add(MessageRow(session_id=state["session_id"], role="assistant", content=reply))
        await db.commit()
    return {"response": reply}
