"""真实 LLM 规划的自然语言端到端演示(需 API 服务已启动)。

运行:.venv/bin/python scripts/demo_e2e.py
"""
import asyncio
import time

import httpx

CASES = [
    ("打开客厅灯。", "简单控制"),
    ("先打开客厅灯,再把亮度调到50%。", "顺序多指令"),
    ("关闭客厅主灯和书房灯。", "并行+离线设备部分成功"),
    ("如果书房灯还开着,就把亮度调到30%。", "条件执行"),
    ("打开床头灯,十分钟后关闭。", "延迟任务"),
    ("卧室灯现在开着吗?", "状态查询"),
    ("把灯调亮。", "信息不足→澄清"),
    ("给我讲个笑话。", "闲聊"),
    ("把客厅主灯调到150%。", "非法参数→拒绝"),
]


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=180) as c:
        # 演示部分成功:书房灯离线
        await c.post("/v1/debug/device-failure", json={"device_id": "dev-study-desk", "mode": "offline"})
        run_id = int(time.time())
        for i, (msg, label) in enumerate(CASES):
            r = await c.post("/v1/chat", json={"session_id": f"s-demo-{run_id}-{i}", "message": msg})
            d = r.json()
            print(f"[{label}] 用户: {msg}")
            print(f"          星火: {d.get('reply', d)}\n")
        await c.post("/v1/debug/device-failure", json={"device_id": "dev-study-desk", "mode": None})


if __name__ == "__main__":
    asyncio.run(main())
