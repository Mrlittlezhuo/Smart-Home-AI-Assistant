"""Home Assistant 适配器(§6.12 路线之一):REST API 接入。

实体映射:light.* → lighting 领域,cover.* → curtain 领域;
area → 房间,friendly_name → 名称。HA 不支持幂等键,适配器本地缓存去重。
与 Mock 适配器遵守同一 DeviceAdapter 契约,通过同一套契约测试。
"""
import logging
from typing import Any

import httpx

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
    DeviceInfo,
    DeviceState,
    now_utc,
)

logger = logging.getLogger(__name__)

_CAP_ON = Capability(name=CAP_ON_OFF)
_CAP_BRIGHT = Capability(name=CAP_BRIGHTNESS, min_value=1, max_value=100, unit="%")
_CAP_CT = Capability(name=CAP_COLOR_TEMP, min_value=1700, max_value=6500, unit="K")
_CAP_COLOR = Capability(name=CAP_COLOR)
_CAP_POS = Capability(name=CAP_POSITION, min_value=0, max_value=100, unit="%")


class HomeAssistantAdapter(DeviceAdapter):
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        self._idempotency: dict[str, CommandResult] = {}
        self._catalog_cache: list[DeviceInfo] | None = None

    async def list_devices(self, home_id: str) -> list[DeviceInfo]:
        resp = await self._client.get("/api/states")
        resp.raise_for_status()
        out: list[DeviceInfo] = []
        for ent in resp.json():
            entity_id = ent.get("entity_id", "")
            prefix = entity_id.split(".", 1)[0]
            attrs = ent.get("attributes") or {}
            if prefix == "light":
                modes = attrs.get("supported_color_modes") or []
                caps = [_CAP_ON, _CAP_BRIGHT]
                if {"color_temp", "color_temp_kelvin"} & set(modes):
                    caps.append(_CAP_CT)
                if {"rgb", "rgbw", "rgbww", "hs"} & set(modes):
                    caps.append(_CAP_COLOR)
                domain = "lighting"
            elif prefix == "cover":
                caps, domain = [_CAP_POS], "curtain"
            else:
                continue
            area = attrs.get("area") or ""
            out.append(DeviceInfo(
                device_id=entity_id, home_id=home_id,
                room_id=area or entity_id, room_name=area or "未分组",
                name=attrs.get("friendly_name") or entity_id,
                domain=domain, aliases=[], capabilities=caps,
            ))
        self._catalog_cache = out
        return out

    async def get_state(self, device_id: str) -> DeviceState:
        resp = await self._client.get(f"/api/states/{device_id}")
        if resp.status_code == 404:
            raise KeyError(device_id)
        ent = resp.json()
        attrs = ent.get("attributes") or {}
        value = ent.get("state")
        st: dict[str, Any] = {}
        if value in ("on", "off"):
            st["on"] = value == "on"
        if attrs.get("brightness") is not None:
            st["brightness_pct"] = round(attrs["brightness"] / 255 * 100)
        if attrs.get("color_temp_kelvin") is not None:
            st["color_temp_k"] = int(attrs["color_temp_kelvin"])
        elif attrs.get("color_temp") is not None:
            st["color_temp_k"] = int(1_000_000 / attrs["color_temp"])
        if attrs.get("current_position") is not None:
            st["position_pct"] = attrs["current_position"]
        return DeviceState(
            device_id=device_id,
            online=value not in ("unavailable", "unknown"),
            state=st,
            observed_at=now_utc(),
        )

    async def set_state(
        self, device_id: str, desired_state: dict[str, Any], idempotency_key: str
    ) -> CommandResult:
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        result = await self._apply(device_id, desired_state)
        self._idempotency[idempotency_key] = result
        return result

    async def _apply(self, device_id: str, desired: dict[str, Any]) -> CommandResult:
        # 能力检查(与 Mock 同一语义)
        if self._catalog_cache is None:
            await self.list_devices("h1")
        device = next((d for d in (self._catalog_cache or []) if d.device_id == device_id), None)
        if device is None:
            return CommandResult(ok=False, status="failed", error="unknown_device")
        caps = {c.name for c in device.capabilities}
        arg_cap = {"on": CAP_ON_OFF, "brightness_pct": CAP_BRIGHTNESS,
                   "color_temp_k": CAP_COLOR_TEMP, "color_hex": CAP_COLOR,
                   "position_pct": CAP_POSITION}
        for key in desired:
            cap = arg_cap.get(key)
            if cap is None or cap not in caps:
                return CommandResult(ok=False, status="failed", error=f"unsupported:{key}")

        try:
            cur = await self.get_state(device_id)
        except KeyError:
            return CommandResult(ok=False, status="failed", error="unknown_device")
        if not cur.online:
            return CommandResult(ok=False, status="failed", error="device_offline")

        prefix = device_id.split(".", 1)[0]
        try:
            if prefix == "light":
                if desired.get("on") is False and len(desired) == 1:
                    await self._client.post("/api/services/light/turn_off", json={"entity_id": device_id})
                else:
                    body: dict[str, Any] = {"entity_id": device_id}
                    if "brightness_pct" in desired:
                        body["brightness_pct"] = desired["brightness_pct"] / 100
                    if "color_temp_k" in desired:
                        body["color_temp_kelvin"] = desired["color_temp_k"]
                    if "color_hex" in desired:
                        body["rgb_color"] = _hex_to_rgb(desired["color_hex"])
                    await self._client.post("/api/services/light/turn_on", json=body)
            elif prefix == "cover":
                pos = desired.get("position_pct", 100)
                if pos == 0:
                    await self._client.post("/api/services/cover/close_cover", json={"entity_id": device_id})
                elif pos == 100:
                    await self._client.post("/api/services/cover/open_cover", json={"entity_id": device_id})
                else:
                    await self._client.post("/api/services/cover/set_cover_position",
                                            json={"entity_id": device_id, "position": pos})
            else:
                return CommandResult(ok=False, status="failed", error=f"unsupported_entity:{prefix}")
        except httpx.HTTPError as e:
            logger.warning("HA service call failed for %s: %s", device_id, e)
            return CommandResult(ok=False, status="failed", error="device_timeout")

        # 回读校验(§8.2 verified)
        new = await self.get_state(device_id)
        if _matches(desired, new.state):
            return CommandResult(ok=True, status="verified", state=new.state)
        return CommandResult(ok=True, status="applied", state=new.state)


def _hex_to_rgb(color_hex: str) -> list[int]:
    h = color_hex.lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]


def _matches(desired: dict, actual: dict) -> bool:
    for key, want in desired.items():
        got = actual.get(key)
        if key in ("brightness_pct", "position_pct"):
            if got is None or abs(got - want) > 3:
                return False
        elif key == "color_temp_k":
            if got is None or abs(got - want) > 100:
                return False
        else:
            if got != want:
                return False
    return True
