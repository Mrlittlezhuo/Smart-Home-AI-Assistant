"""FastAPI 入口:初始化数据库、Mock 设备网关、LLM 网关与带检查点的主图。"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.automations.engine import handle_event
from app.config import settings
from app.db.session import init_db
from app.device_gateway.factory import build_adapter
from app.device_gateway.gateway import DeviceGateway
from app.graph.main_graph import build_graph
from app.llm.gateway import LLMGateway
from app.seed import seed_builtin
from app.services import Services, set_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    cm = AsyncPostgresSaver.from_conn_string(settings.checkpoint_pg_dsn)
    checkpointer = await cm.__aenter__()
    await checkpointer.setup()

    svc = Services(
        gateway=DeviceGateway(build_adapter()),
        llm=LLMGateway(),
    )
    svc.graph = build_graph(checkpointer=checkpointer)
    set_services(svc)
    # 事件驱动管道(§4.3):设备事件 → 去重 → 状态更新 → 已启用自动化
    svc.gateway.adapter.subscribe_events(handle_event)
    await seed_builtin()
    logger.info("whole-home agent ready (adapter=%s)", settings.device_adapter)
    yield
    await cm.__aexit__(None, None, None)


app = FastAPI(title="Whole-Home Agent", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


# 星火终端前端(阶段零):纯静态,只消费结构化交互状态
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent / "web", html=True), name="web")
