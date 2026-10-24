"""DeviceAdapter 契约(§6.10)。Mock 与真实适配器必须遵守同一契约。"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from app.capabilities.models import Capability


class DeviceInfo(BaseModel):
    device_id: str
    home_id: str
    room_id: str
    room_name: str
    name: str
    domain: str = "lighting"
    aliases: list[str] = []
    capabilities: list[Capability] = []


class DeviceState(BaseModel):
    device_id: str
    online: bool
    state: dict[str, Any]
    observed_at: datetime


class CommandResult(BaseModel):
    ok: bool
    # accepted / applied / verified / failed(§8.2)
    status: str
    state: dict[str, Any] | None = None
    error: str | None = None


class DeviceEvent(BaseModel):
    event_id: str
    device_id: str
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class DeviceAdapter(ABC):
    @abstractmethod
    async def list_devices(self, home_id: str) -> list[DeviceInfo]: ...

    @abstractmethod
    async def get_state(self, device_id: str) -> DeviceState: ...

    @abstractmethod
    async def set_state(
        self, device_id: str, desired_state: dict[str, Any], idempotency_key: str
    ) -> CommandResult: ...

    def subscribe_events(self, callback: Callable[[DeviceEvent], Awaitable[None]]) -> None:
        self._subscribers = getattr(self, "_subscribers", [])
        self._subscribers.append(callback)

    async def _emit(self, event: DeviceEvent) -> None:
        for cb in getattr(self, "_subscribers", []):
            await cb(event)
