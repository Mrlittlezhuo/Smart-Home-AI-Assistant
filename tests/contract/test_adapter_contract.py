"""适配器契约测试(§6.10 / T-17):Mock 与 HA 适配器必须通过同一套断言。

HA 侧用 httpx.MockTransport 假服务器,无需真实 Home Assistant。
"""
import httpx
import pytest

from app.device_gateway.adapters.base import DeviceAdapter
from app.device_gateway.adapters.home_assistant import HomeAssistantAdapter
from app.device_gateway.adapters.mock import MockDeviceAdapter


async def run_adapter_contract_suite(adapter: DeviceAdapter, home_id: str) -> None:
    """所有 DeviceAdapter 实现必须满足的行为契约。"""
    devices = await adapter.list_devices(home_id)
    lights = [d for d in devices if d.domain == "lighting"]
    assert lights, "目录必须包含照明设备"
    light = next(d for d in lights if not any(c.name == "color" for c in d.capabilities))

    # 状态读取
    st = await adapter.get_state(light.device_id)
    assert st.online and "on" in st.state

    # 写入并回读 verified
    res = await adapter.set_state(light.device_id, {"on": True, "brightness_pct": 100}, "k-c1")
    assert res.ok and res.status == "verified"
    st2 = await adapter.get_state(light.device_id)
    assert st2.state["on"] is True
    assert abs(st2.state["brightness_pct"] - 100) <= 3

    # 幂等键:相同键重放不得再次执行(§8.2)
    res2 = await adapter.set_state(light.device_id, {"on": False}, "k-c1")
    assert res2.ok
    st3 = await adapter.get_state(light.device_id)
    assert st3.state["on"] is True, "重放同一幂等键不应改变设备状态"

    # 不支持的能力 → 明确失败
    res3 = await adapter.set_state(light.device_id, {"color_hex": "#FF0000"}, "k-c2")
    assert not res3.ok and res3.error.startswith("unsupported")

    # 未知设备
    res4 = await adapter.set_state("no-such-device", {"on": True}, "k-c3")
    assert not res4.ok and res4.error == "unknown_device"

    # 窗帘领域:位置控制
    curtains = [d for d in devices if d.domain == "curtain"]
    assert curtains, "目录必须包含窗帘设备"
    res5 = await adapter.set_state(curtains[0].device_id, {"position_pct": 40}, "k-c4")
    assert res5.ok and res5.status == "verified"
    st4 = await adapter.get_state(curtains[0].device_id)
    assert abs(st4.state["position_pct"] - 40) <= 3


async def test_mock_adapter_satisfies_contract():
    await run_adapter_contract_suite(MockDeviceAdapter(), "h1")


class FakeHomeAssistant:
    """最小 HA REST 语义:/api/states 与 /api/services/<domain>/<service>。"""

    def __init__(self):
        self.entities: dict[str, dict] = {
            "light.bedside": {
                "entity_id": "light.bedside", "state": "off",
                "attributes": {"friendly_name": "床头灯", "area": "卧室",
                               "supported_color_modes": ["brightness"], "brightness": 128},
            },
            "cover.living": {
                "entity_id": "cover.living", "state": "open",
                "attributes": {"friendly_name": "客厅窗帘", "area": "客厅", "current_position": 100},
            },
            "sensor.motion": {"entity_id": "sensor.motion", "state": "off", "attributes": {}},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/api/states":
            return httpx.Response(200, json=list(self.entities.values()))
        if request.method == "GET" and path.startswith("/api/states/"):
            ent = self.entities.get(path.removeprefix("/api/states/"))
            if ent is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=ent)
        if request.method == "POST" and path.startswith("/api/services/"):
            _, _, _, domain, service = path.split("/")
            body = body_json(request) if request.content else {}
            ent = self.entities.get(body.get("entity_id", ""))
            if ent is None:
                return httpx.Response(404, json={"message": "not found"})
            if domain == "light" and service == "turn_on":
                ent["state"] = "on"
                if "brightness_pct" in body:
                    ent["attributes"]["brightness"] = round(body["brightness_pct"] * 255)
                if "color_temp_kelvin" in body:
                    ent["attributes"]["color_temp_kelvin"] = body["color_temp_kelvin"]
            elif domain == "light" and service == "turn_off":
                ent["state"] = "off"
            elif domain == "cover" and service == "close_cover":
                ent["state"], ent["attributes"]["current_position"] = "closed", 0
            elif domain == "cover" and service == "open_cover":
                ent["state"], ent["attributes"]["current_position"] = "open", 100
            elif domain == "cover" and service == "set_cover_position":
                ent["state"] = "opening"
                ent["attributes"]["current_position"] = body["position"]
            else:
                return httpx.Response(404, json={"message": "unknown service"})
            return httpx.Response(200, json={"entities": [ent["entity_id"]]})
        return httpx.Response(404, json={"message": "not found"})


def body_json(request: httpx.Request) -> dict:
    import json
    return json.loads(request.content)


def _fake_ha_adapter(fake: FakeHomeAssistant) -> HomeAssistantAdapter:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handle), base_url="http://fake-ha")
    return HomeAssistantAdapter(base_url="http://fake-ha", token="test", client=client)


async def test_ha_adapter_satisfies_contract():
    await run_adapter_contract_suite(_fake_ha_adapter(FakeHomeAssistant()), "h1")


async def test_ha_entity_mapping():
    fake = FakeHomeAssistant()
    adapter = _fake_ha_adapter(fake)
    devices = await adapter.list_devices("h1")
    by_id = {d.device_id: d for d in devices}
    assert "sensor.motion" not in by_id  # 未接入的实体类型被忽略
    assert by_id["light.bedside"].domain == "lighting"
    assert by_id["light.bedside"].name == "床头灯"
    assert by_id["light.bedside"].room_name == "卧室"
    assert by_id["cover.living"].domain == "curtain"
    caps = {c.name for c in by_id["light.bedside"].capabilities}
    assert caps == {"on_off", "brightness"}  # 无 color_temp/rgb 模式


async def test_ha_unavailable_maps_to_offline():
    fake = FakeHomeAssistant()
    fake.entities["light.bedside"]["state"] = "unavailable"
    adapter = _fake_ha_adapter(fake)
    res = await adapter.set_state("light.bedside", {"on": True}, "k-off")
    assert not res.ok and res.error == "device_offline"


async def test_ha_http_error_maps_to_timeout():
    def fail(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/services/"):
            raise httpx.ConnectError("connection refused")
        return FakeHomeAssistant().handle(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail), base_url="http://fake-ha")
    adapter = HomeAssistantAdapter(base_url="http://fake-ha", token="t", client=client)
    res = await adapter.set_state("light.bedside", {"on": True}, "k-err")
    assert not res.ok and res.error == "device_timeout"
