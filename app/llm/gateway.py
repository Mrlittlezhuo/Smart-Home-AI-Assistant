"""LLM 网关(§6.5):所有模型调用收口于此,供应商可替换。

当前对接 Anthropic 兼容接口(strata)。真实工具调用永远不发生在模型侧。
"""
import logging

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self) -> None:
        self._client = AsyncAnthropic(
            base_url=settings.llm_base_url,
            auth_token=settings.llm_auth_token,
            timeout=settings.llm_timeout_s,
            max_retries=0,
        )

    async def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> str:
        resp = await self._client.messages.create(
            model=settings.llm_model,
            max_tokens=max_tokens or settings.llm_max_tokens,
            system=system,
            messages=messages,
        )
        parts = [b.text for b in resp.content if b.type == "text"]
        return "".join(parts)
