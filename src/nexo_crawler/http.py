"""Shared HTTP behavior used by every source adapter."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_USER_AGENT = "NEXO-Database-Crawler/0.2 (source-grounded research dataset)"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class HttpResponse:
    data: bytes
    content_type: str | None
    final_url: str


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float,
        retries: int,
        request_delay: float,
        user_agent: str,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.request_delay = request_delay
        self.user_agent = user_agent
        self._last_request_started: float | None = None

    def _throttle(self) -> None:
        if self._last_request_started is not None:
            elapsed = time.monotonic() - self._last_request_started
            if elapsed < self.request_delay:
                time.sleep(self.request_delay - elapsed)
        self._last_request_started = time.monotonic()

    def get(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json,image/*;q=0.9,*/*;q=0.8",
                    "User-Agent": self.user_agent,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return HttpResponse(
                        data=response.read(),
                        content_type=response.headers.get_content_type(),
                        final_url=response.geturl(),
                    )
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in RETRYABLE_STATUS_CODES or attempt >= self.retries:
                    raise
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 30.0))
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt >= self.retries:
                    raise
                time.sleep(min(2**attempt, 30.0))
        raise RuntimeError(f"request failed: {last_error}")

    def get_json(self, url: str) -> dict[str, Any]:
        response = self.get(url)
        try:
            value = json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid JSON response from {url}") from error
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object from {url}")
        return value
