"""按配置选择设备适配器(§6.12):Mock 用于开发与验证,HA 用于真实设备。"""
import logging

from app.config import settings
from app.device_gateway.adapters.base import DeviceAdapter
from app.device_gateway.adapters.mock import MockDeviceAdapter

logger = logging.getLogger(__name__)


def build_adapter() -> DeviceAdapter:
    if settings.device_adapter == "home_assistant":
        if not settings.ha_base_url or not settings.ha_token:
            raise RuntimeError("device_adapter=home_assistant 需要 HA_BASE_URL 与 HA_TOKEN")
        from app.device_gateway.adapters.home_assistant import HomeAssistantAdapter

        logger.info("using Home Assistant adapter at %s", settings.ha_base_url)
        return HomeAssistantAdapter(settings.ha_base_url, settings.ha_token)
    logger.info("using MockDeviceAdapter")
    return MockDeviceAdapter()
