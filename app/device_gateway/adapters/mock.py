"""MockDeviceAdapter(§6.10.1)。

模拟可观察的设备行为:目录/别名/能力、状态更新与回执、主动事件、
离线/超时/拒绝故障注入、幂等键去重、测试用状态重置。
"""
import uuid
from typing import Any

from app.capabilities.models import (
    CAP_BRIGHTNESS,
    CAP_COLOR,
    CAP_COLOR_TEMP,
    CAP_ON_OFF,
    CAP_POSITION,
    Capability,
)
from app.device_gateway.adapters.base import (
    CommandResult,
    DeviceAdapter,
    DeviceEvent,
    DeviceInfo,
    DeviceState,
    now_utc,
)


def _cap(name: str, lo: float | None = None, hi: float | None = None, unit: str | None = None) -> Capability:
    return Capability(name=name, min_value=lo, max_value=hi, unit=unit)


DEFAULT_CATALOG: list[DeviceInfo] = [
    DeviceInfo(
        device_id="dev-living-main", home_id="h1", room_id="room-living", room_name="客厅",
        name="客厅主灯", aliases=["客厅灯", "大厅灯"],
        capabilities=[_cap(CAP_ON_OFF), _cap(CAP_BRIGHTNESS, 1, 100, "%"), _cap(CAP_COLOR_TEMP, 2700, 6500, "K")],
    ),
    DeviceInfo(
        device_id="dev-living-strip", home_id="h1", room_id="room-living", room_name="客厅",
        name="客厅灯带", aliases=["灯带"],
        capabilities=[_cap(CAP_ON_OFF), _cap(CAP_BRIGHTNESS, 1, 100, "%"), _cap(CAP_COLOR)],
    ),
    DeviceInfo(
        device_id="dev-bedroom-main", home_id="h1", room_id="room-bedroom", room_name="卧室",
        name="卧室主灯", aliases=["卧室灯"],
        capabilities=[_cap(CAP_ON_OFF), _cap(CAP_BRIGHTNESS, 1, 100, "%"), _cap(CAP_COLOR_TEMP, 2700, 6500, "K")],
    ),
    DeviceInfo(
        device_id="dev-bedside", home_id="h1", room_id="room-bedroom", room_name="卧室",
        name="床头灯", aliases=["床头小灯"],
        capabilities=[_cap(CAP_ON_OFF), _cap(CAP_BRIGHTNESS, 1, 100, "%")],
    ),
    DeviceInfo(
        device_id="dev-study-desk", home_id="h1", room_id="room-study", room_name="书房",
        name="书房台灯", aliases=["书房灯", "台灯"],
        capabilities=[_cap(CAP_ON_OFF), _cap(CAP_BRIGHTNESS, 1, 100, "%")],
    ),
    # 第二个模拟领域:窗帘(T-17)
    DeviceInfo(
        device_id="dev-living-curtain", home_id="h1", room_id="room-living", room_name="客厅",
        name="客厅窗帘", domain="curtain", aliases=["客厅窗帘", "窗帘"],
        capabilities=[_cap(CAP_POSITION, 0, 100, "%")],
    ),
    DeviceInfo(
        device_id="dev-bedroom-curtain", home_id="h1", room_id="room-bedroom", room_name="卧室",
        name="卧室窗帘", domain="curtain", aliases=["卧室窗帘"],
        capabilities=[_cap(CAP_POSITION, 0, 100, "%")],
    ),
    # 事件驱动验证用传感器(T-19)
    DeviceInfo(
        device_id="dev-living-motion", home_id="h1", room_id="room-living", room_name="客厅",
        name="客厅人体传感器", domain="sensor", aliases=["人体传感器"],
        capabilities=[],
    ),
]

def _default_state_for(device: DeviceInfo) -> dict[str, Any]:
    caps = {c.name for c in device.capabilities}
    out: dict[str, Any] = {}
    if CAP_ON_OFF in caps:
        out["on"] = False
    if CAP_BRIGHTNESS in caps:
        out["brightness_pct"] = 80
    if CAP_COLOR_TEMP in caps:
        out["color_temp_k"] = 4000
    if CAP_COLOR in caps:
        out["color_hex"] = "#FFFFFF"
    if CAP_POSITION in caps:
        out["position_pct"] = 100
    return out


class MockDeviceAdapter(DeviceAdapter):
    def __init__(self, catalog: list[DeviceInfo] | None = None):
        self._catalog = list(catalog or DEFAULT_CATALOG)
        self._by_id = {d.device_id: d for d in self._catalog}
        self._states: dict[str, dict[str, Any]] = {d.device_id: _default_state_for(d) for d in self._catalog}
        self._online: dict[str, bool] = {d.device_id: True for d in self._catalog}
        # 故障注入:device_id -> offline | timeout | reject
        self._failure: dict[str, str] = {}
        self._idempotency: dict[str, CommandResult] = {}

    # ---- 测试辅助 ----
    def reset(self) -> None:
        self._states = {d.device_id: _default_state_for(d) for d in self._catalog}
        self._online = {d.device_id: True for d in self._catalog}
        self._failure = {}
        self._idempotency = {}

    def set_failure_mode(self, device_id: str, mode: str | None) -> None:
        if mode is None:
            self._failure.pop(device_id, None)
        else:
            self._failure[device_id] = mode

    async def inject_event(self, device_id: str, event_type: str,
                           payload: dict[str, Any] | None = None, event_id: str | None = None) -> None:
        """测试/调试用:模拟传感器等设备主动上报事件。"""
        await self._emit(DeviceEvent(
            event_id=event_id or uuid.uuid4().hex, device_id=device_id,
            event_type=event_type, payload=payload or {}, occurred_at=now_utc(),
        ))

    # ---- 契约实现 ----
    async def list_devices(self, home_id: str) -> list[DeviceInfo]:
        return [d for d in self._catalog if d.home_id == home_id]

    async def get_state(self, device_id: str) -> DeviceState:
        if device_id not in self._by_id:
            raise KeyError(f"unknown device {device_id}")
        return DeviceState(
            device_id=device_id,
            online=self._online[device_id],
            state=dict(self._states[device_id]),
            observed_at=now_utc(),
        )

    async def set_state(
        self, device_id: str, desired_state: dict[str, Any], idempotency_key: str
    ) -> CommandResult:
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        device = self._by_id.get(device_id)
        if device is None:
            return CommandResult(ok=False, status="failed", error="unknown_device")

        failure = self._failure.get(device_id)
        if failure == "offline" or not self._online[device_id]:
            result = CommandResult(ok=False, status="failed", error="device_offline")
        elif failure == "timeout":
            result = CommandResult(ok=False, status="failed", error="device_timeout")
        elif failure == "reject":
            result = CommandResult(ok=False, status="failed", error="device_rejected")
        else:
            caps = {c.name: c for c in device.capabilities}
            applied: dict[str, Any] = {}
            for key, value in desired_state.items():
                cap_name = {
                    "on": CAP_ON_OFF, "brightness_pct": CAP_BRIGHTNESS,
                    "color_temp_k": CAP_COLOR_TEMP, "color_hex": CAP_COLOR,
                    "position_pct": CAP_POSITION,
                }.get(key)
                cap = caps.get(cap_name) if cap_name else None
                if cap is None or not cap.writable:
                    result = CommandResult(ok=False, status="failed", error=f"unsupported:{key}")
                    break
                if cap.min_value is not None and value < cap.min_value:
                    result = CommandResult(ok=False, status="failed", error=f"out_of_range:{key}")
                    break
                if cap.max_value is not None and value > cap.max_value:
                    result = CommandResult(ok=False, status="failed", error=f"out_of_range:{key}")
                    break
                applied[key] = value
            else:
                self._states[device_id].update(applied)
                new_state = dict(self._states[device_id])
                result = CommandResult(ok=True, status="verified", state=new_state)
                await self._emit(DeviceEvent(
                    event_id=uuid.uuid4().hex, device_id=device_id,
                    event_type="state_changed", payload=applied, occurred_at=now_utc(),
                ))

        self._idempotency[idempotency_key] = result
        return result
