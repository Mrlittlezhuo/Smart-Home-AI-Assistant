"""统一设备网关(§6.10):上层不感知具体协议与适配器。"""
from typing import Any

from app.device_gateway.adapters.base import CommandResult, DeviceAdapter, DeviceInfo, DeviceState


class DeviceGateway:
    def __init__(self, adapter: DeviceAdapter):
        self._adapter = adapter
        self._catalog_cache: dict[str, list[DeviceInfo]] = {}

    @property
    def adapter(self) -> DeviceAdapter:
        return self._adapter

    async def list_devices(self, home_id: str, refresh: bool = False) -> list[DeviceInfo]:
        if refresh or home_id not in self._catalog_cache:
            self._catalog_cache[home_id] = await self._adapter.list_devices(home_id)
        return self._catalog_cache[home_id]

    async def get_state(self, device_id: str) -> DeviceState:
        return await self._adapter.get_state(device_id)

    async def set_state(
        self, device_id: str, desired_state: dict[str, Any], idempotency_key: str
    ) -> CommandResult:
        return await self._adapter.set_state(device_id, desired_state, idempotency_key)
