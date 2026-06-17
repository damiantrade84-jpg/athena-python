from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import requests


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: Mapping[str, str]
    url: str

    def raise_for_status(self) -> None:
        if 200 <= self.status_code < 300:
            return
        raise RuntimeError(f"provider HTTP {self.status_code}")


class HttpGet(Protocol):
    def __call__(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse: ...


def requests_get(
    url: str,
    *,
    params: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    return HttpResponse(
        status_code=response.status_code,
        content=response.content,
        headers=dict(response.headers),
        url=response.url,
    )
