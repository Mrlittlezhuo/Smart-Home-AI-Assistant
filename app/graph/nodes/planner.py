"""LLM 规划节点(§7.1 单次结构化规划)。

一次调用产出完整候选计划;解析失败带错误反馈重试一次;
仍失败则降级为“没听懂”,不产生任何副作用。
"""
import hashlib
import json
import logging
import re
import uuid
from pathlib import Path

from pydantic import ValidationError

from app.config import settings
from app.db.models import PlanRow
from app.db.session import get_sessionmaker
from app.graph.schemas import TaskPlan
from app.graph.state import AgentState
from app.services import get_services

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "planner.md").read_text(encoding="utf-8")

FALLBACK_REPLY = "抱歉,我没能理解这个请求,可以换一种说法吗?"


def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型输出中没有 JSON 对象")
    return json.loads(text[start : end + 1])


def build_context_package(state: AgentState) -> str:
    parts: list[str] = []
    catalog = state.get("device_catalog") or []
    if catalog:
        lines = []
        for d in catalog:
            caps = ",".join(c["name"] for c in d.get("capabilities", []))
            alias = ",".join(d.get("aliases", []))
            lines.append(f"- {d['room_name']}|{d['name']}|领域:{d.get('domain','lighting')}|别名:{alias}|能力:{caps}")
        parts.append("## 设备目录\n" + "\n".join(lines))
    targets = state.get("recent_targets") or []
    if targets:
        parts.append("## 最近操作对象\n" + "\n".join(f"- {t['name']}" for t in targets))
    scenes = state.get("scenes") or []
    if scenes:
        parts.append("## 可用场景\n" + "、".join(scenes))
    prefs = state.get("user_preferences") or []
    if prefs:
        parts.append("## 已保存的个人偏好\n" + "\n".join(
            f"- {p['scope_type']}:{p['scope_name'] or p['scope_id'] or '全局'}|{p['preference_key']}={p['value']}"
            for p in prefs
        ))
    turns = state.get("recent_turns") or []
    if turns:
        lines = [f"{t['role']}: {t['content']}" for t in turns]
        parts.append("## 最近对话\n" + "\n".join(lines))
    pkg = "\n\n".join(parts)
    budget = settings.context_char_budget
    if len(pkg) > budget:
        pkg = pkg[:budget]
    return pkg


async def planner_node(state: AgentState) -> dict:
    svc = get_services()
    plan: TaskPlan | None = None

    if state.get("fixed_plan") is not None:
        # 阶段一/测试路径:不接 LLM,直接验证主图链路
        try:
            plan = TaskPlan.model_validate(state["fixed_plan"])
        except ValidationError as e:
            return {"candidate_plan": None, "response": FALLBACK_REPLY}
    else:
        user_msg = f"{build_context_package(state)}\n\n## 当前用户输入\n{state['current_input']}\n\n请输出候选任务计划 JSON。"
        messages = [{"role": "user", "content": user_msg}]
        for attempt in range(1 + settings.llm_plan_max_retries):
            try:
                raw = await svc.llm.complete(_PROMPT, messages)
                plan = TaskPlan.model_validate(extract_json(raw))
                break
            except (ValueError, json.JSONDecodeError, ValidationError) as e:
                logger.warning("plan parse failed (attempt %d): %s", attempt + 1, e)
                messages = [
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": raw if attempt == 0 else ""},
                    {"role": "user", "content": f"输出无效:{e}。请只输出符合 Schema 的 JSON。"},
                ]
        if plan is None:
            return {"candidate_plan": None, "response": FALLBACK_REPLY}

    dumped = plan.model_dump()
    plan_id = uuid.uuid4().hex
    plan_hash = hashlib.sha256(
        json.dumps(dumped, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()

    sm = get_sessionmaker()
    async with sm() as db:
        db.add(PlanRow(
            plan_id=plan_id, session_id=state["session_id"],
            user_id=state["user_id"], home_id=state["home_id"],
            raw_plan=dumped, plan_hash=plan_hash, status="candidate",
        ))
        await db.commit()

    return {
        "candidate_plan": dumped,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "needs_clarification": plan.needs_clarification,
        "clarification_question": plan.clarification_question,
    }
