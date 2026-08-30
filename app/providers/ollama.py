from __future__ import annotations

import httpx

from app.core.errors import PROVIDER_UNAVAILABLE
from app.models import UsageMeasurement
from app.providers.base import TextResult


class OllamaProvider:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        *,
        model: str,
        system_instruction: str,
        input_text: str,
        response_format: str,
        temperature: float,
    ) -> TextResult:
        try:
            response = await self._client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json" if response_format == "json" else "",
                    "options": {"temperature": temperature},
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": input_text},
                    ],
                },
            )
            response.raise_for_status()
            value = response.json()
            output = value["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise PROVIDER_UNAVAILABLE from error
        if not isinstance(output, str) or not output.strip():
            raise PROVIDER_UNAVAILABLE
        return TextResult(
            output=output.strip(),
            usage=UsageMeasurement(
                input_tokens=_int(value.get("prompt_eval_count")),
                output_tokens=_int(value.get("eval_count")),
            ),
        )


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
