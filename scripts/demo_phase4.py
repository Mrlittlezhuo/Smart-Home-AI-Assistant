"""阶段四演示:个人偏好 + interrupt 确认流(需 API 服务已启动)。

运行:.venv/bin/python scripts/demo_phase4.py
"""
import asyncio
import time

import httpx


async def main() -> None:
    run = int(time.time())
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=180) as c:
        async def chat(sid, msg):
            r = await c.post("/v1/chat", json={"session_id": sid, "message": msg})
            return r.json()

        s1 = f"s-p4-{run}-pref"
        for msg in [
            "记住,以后卧室灯默认调到30%。",
            "打开卧室灯。",                      # 偏好补全亮度
            "把卧室灯调到70%。",                 # 当前指令覆盖偏好
            "你记住了我什么?",                   # 偏好查询
            "删除卧室灯的亮度偏好。",             # 偏好删除
        ]:
            d = await chat(s1, msg)
            print(f"用户: {msg}\n星火: {d.get('reply', d)}\n")

        s2 = f"s-p4-{run}-conf"
        d = await chat(s2, "关闭家里所有灯。")
        print(f"用户: 关闭家里所有灯。\n星火: {d.get('reply')} (awaiting={d.get('awaiting_confirmation')})")
        r = await c.post(f"/v1/plans/{d['plan_id']}/confirm",
                         json={"plan_hash": d["plan_hash"], "confirmed": True})
        print(f"用户: [确认]\n星火: {r.json().get('reply')}\n")

        d = await chat(f"s-p4-{run}-cancel", "关闭家里所有灯。")
        r = await c.post(f"/v1/plans/{d['plan_id']}/confirm",
                         json={"plan_hash": d["plan_hash"], "confirmed": False})
        print(f"用户: 关闭家里所有灯。\n星火: {d.get('reply')}\n用户: [取消]\n星火: {r.json().get('reply')}\n")

        prefs = await c.get("/v1/preferences")
        print("GET /v1/preferences →", prefs.json())


if __name__ == "__main__":
    asyncio.run(main())
