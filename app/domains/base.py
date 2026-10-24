"""领域子图契约(§6.3)。

主图只依赖契约的输入输出;新增领域注册新插件,不修改主图。
首版以插件对象实现契约,后续可原位替换为 LangGraph 子图。
"""
from abc import ABC, abstractmethod

from app.device_gateway.adapters.base import DeviceInfo
from app.graph.schemas import PlanTask


class DomainPlugin(ABC):
    name: str
    # 设备领域为 True:目标解析按 device.domain == name 过滤
    needs_devices: bool = True

    @abstractmethod
    def task_types(self) -> set[str]:
        """该领域允许的任务类型(未注册类型必须被校验器拒绝)。"""

    @abstractmethod
    def validate_task(self, task: PlanTask, devices: list[DeviceInfo]) -> list[str]:
        """领域级校验,返回错误说明列表;空列表表示通过。"""

    def handlers(self) -> dict:
        """任务类型 → 执行处理器(工具)。副作用处理器必须自行保证幂等。"""
        return {}
