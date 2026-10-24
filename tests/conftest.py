import pytest

from app.automations.engine import handle_event
from app.db.base import Base
from app.db.session import get_engine, init_db
from app.device_gateway.adapters.mock import MockDeviceAdapter
from app.device_gateway.gateway import DeviceGateway
from app.llm.gateway import LLMGateway
from app.seed import seed_builtin
from app.services import Services, set_services


@pytest.fixture
async def services():
    """每个测试:重建表、注入 Mock 适配器服务、接通事件管道并播种内置场景/自动化。"""
    await init_db()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()

    adapter = MockDeviceAdapter()
    svc = Services(gateway=DeviceGateway(adapter), llm=LLMGateway())
    set_services(svc)
    adapter.subscribe_events(handle_event)
    await seed_builtin()
    yield svc, adapter
    # pytest-asyncio 每个测试一个新事件循环,必须释放绑定在旧循环上的连接池
    await get_engine().dispose()


def make_state(**overrides) -> dict:
    state = {
        "session_id": "s-test",
        "user_id": "u1",
        "home_id": "h1",
        "timezone": "Asia/Shanghai",
        "current_input": "测试输入",
        "fixed_plan": None,
        "device_catalog": [],
        "recent_turns": [],
        "recent_targets": [],
        "candidate_plan": None,
        "plan_id": "p-test",
        "plan_hash": "h-test",
        "validation": None,
        "execution_results": [],
        "needs_clarification": False,
        "clarification_question": None,
        "response": None,
    }
    state.update(overrides)
    return state
