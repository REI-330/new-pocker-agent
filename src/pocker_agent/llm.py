from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .configuration import ModelConfig


class ModelClient(Protocol):
    def complete(self, messages: list[dict[str, str]], *, response_format: dict[str, Any] | None = None) -> str: ...


@dataclass
class OpenAICompatibleClient:
    api_key: str = field(repr=False)
    model: str
    base_url: str
    timeout_seconds: float = 90

    @classmethod
    def from_config(cls, config: ModelConfig):
        return cls(config.api_key, config.model, config.base_url)

    def _sdk(self) -> OpenAI:
        if not self.api_key:
            raise RuntimeError("请先保存模型配置")
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds, max_retries=0)

    def _failure(self, error: Exception) -> RuntimeError:
        if isinstance(error, APIStatusError):
            # Redact before truncation so the length cap cannot reveal a key prefix.
            detail = str(error.body).replace(self.api_key, "[REDACTED]")[:500]
            return RuntimeError(f"模型服务 HTTP {error.status_code}: {detail}")
        if isinstance(error, APITimeoutError):
            return RuntimeError("模型服务超时，请稍后重试")
        if isinstance(error, APIConnectionError):
            return RuntimeError("无法连接模型服务，请检查 API 地址和网络")
        return RuntimeError("模型服务返回了无效响应，请检查 API 地址是否指向兼容 API")

    def complete(self, messages: list[dict[str, str]], *, response_format: dict[str, Any] | None = None) -> str:
        # SDK owns HTTP/authentication/error parsing. Omit temperature for models that reject it.
        try:
            with self._sdk() as sdk:
                result = sdk.chat.completions.create(
                    model=self.model, messages=messages, stream=False,
                    **({"response_format": response_format} if response_format else {}),
                )
                content = result.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty model message")
                return content
        except (APIStatusError, APIConnectionError, ValueError, TypeError, AttributeError, IndexError) as error:
            raise self._failure(error) from error

    def list_models(self) -> list[str]:
        try:
            with self._sdk() as sdk:
                # Inspect raw JSON to accept common compatible gateway envelopes.
                response = sdk.models.with_raw_response.list()
                body = response.http_response.json()
            candidates = body.get("data", body.get("models")) if isinstance(body, dict) else body
            if not isinstance(candidates, list):
                raise ValueError("expected model array")
            names = set()
            for item in candidates:
                value = item if isinstance(item, str) else next(
                    (item[key] for key in ("id", "name", "model") if isinstance(item.get(key), str)), None
                ) if isinstance(item, dict) else None
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("invalid model entry")
                names.add(value.strip())
            return sorted(names)
        except (APIStatusError, APIConnectionError, ValueError, TypeError, AttributeError) as error:
            raise self._failure(error) from error
