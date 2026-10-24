"""阶段五演示:跨领域(窗帘/场景)与事件自动化(T-17/T-18/T-19)。

运行:.venv/bin/python scripts/demo_phase5.py  (需 API 服务已启动)
"""
import asyncio
import time

import httpx


async def chat(c: httpx.AsyncClient, session: str, msg: str) -> dict:
    r = await c.post("/v1/chat", json={"session_id": session, "message": msg})
    d = r.json()
    print(f"用户: {msg}")
    print(f"星火: {d.get('reply', d)}")
    if d.get("awaiting_confirmation"):
        print(f"      [待确认 plan_id={d['plan_id']} hash={d['plan_hash'][:12]}…]")
    print()
    return d


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=180) as c:
        run = int(time.time())
        s = f"s-p5-{run}"

        print("== 1. 窗帘领域(T-17:新领域走同一主图) ==")
        await chat(c, s, "打开客厅的灯。")
        await chat(c, s, "把客厅窗帘打开一半。")
        await chat(c, s, "客厅窗帘现在是什么状态?")

        print("== 2. 跨领域场景“离家模式”(T-18:统一确认/执行/汇总) ==")
        d = await chat(c, s, "启动离家模式。")
        if d.get("awaiting_confirmation"):
            r = await c.post(f"/v1/plans/{d['plan_id']}/confirm",
                             json={"plan_hash": d["plan_hash"], "confirmed": True})
            print(f"确认执行 → 星火: {r.json()['reply']}\n")

        print("== 3. 事件与自动化(T-19:去重/条件/触发) ==")
        await chat(c, s, "关闭客厅主灯。")
        # 传感器事件 → 已启用自动化(有人且灯未开 → 开灯)
        await c.post("/v1/debug/device-event", json={
            "device_id": "dev-living-motion", "event_type": "motion_detected", "event_id": f"evt-{run}"})
        print("      [已注入事件 evt-%s:客厅人体传感器 motion_detected]\n" % run)
        await chat(c, s, "客厅主灯现在开着吗?")
        # 同一 event_id 重复上报 → 去重,不重复触发
        await c.post("/v1/debug/device-event", json={
            "device_id": "dev-living-motion", "event_type": "motion_detected", "event_id": f"evt-{run}"})
        print(f"      [重复注入同一 evt-{run}:事件去重,自动化不重复触发]\n")

        print("== 4. Home Assistant 适配器 ==")
        print("真实 HA 接入通过 DEVICE_ADAPTER=home_assistant + HA_BASE_URL/HA_TOKEN 启用;")
        print("与 Mock 共用同一契约测试:pytest tests/contract\n")


if __name__ == "__main__":
    asyncio.run(main())
