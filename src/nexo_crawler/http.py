"""Shared HTTP behavior used by every source adapter."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_USER_AGENT = "NEXO-Database-Crawler/0.5 (source-grounded research dataset)"
RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}


class HttpAccessBlockedError(RuntimeError):
    """Raised after repeated HTTP 403 responses indicate source-wide access blocking."""

    def __init__(self, url: str, attempts: int) -> None:
        self.url = url
        self.attempts = attempts
        super().__init__(
            f"source access remained blocked with HTTP 403 after {attempts} attempt(s): {url}"
        )


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
        """Configure request timeout, retry policy, throttling, and User-Agent."""
        self.timeout = timeout
        self.retries = retries
        self.request_delay = request_delay
        self._effective_request_delay = request_delay
        self.user_agent = user_agent
        self._last_request_started: float | None = None

    def _throttle(self) -> None:
        """Sleep until the minimum delay since the last request has elapsed."""
        if self._last_request_started is not None:
            elapsed = time.monotonic() - self._last_request_started
            if elapsed < self._effective_request_delay:
                time.sleep(self._effective_request_delay - elapsed)
        self._last_request_started = time.monotonic()

    def get(self, url: str) -> HttpResponse:
        """Fetch a URL with throttling and retries for transient HTTP/network errors."""
        last_error: Exception | None = None
        saw_access_block = False
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
                    if saw_access_block:
                        self._effective_request_delay = max(self._effective_request_delay, 1.0)
                    return HttpResponse(
                        data=response.read(),
                        content_type=response.headers.get_content_type(),
                        final_url=response.geturl(),
                    )
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code == 403:
                    saw_access_block = True
                if error.code == 403 and attempt >= self.retries:
                    raise HttpAccessBlockedError(url, attempt + 1) from error
                if error.code not in RETRYABLE_STATUS_CODES or attempt >= self.retries:
                    raise
                retry_after = (
                    error.headers.get("Retry-After") if error.headers is not None else None
                )
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
                elif error.code == 403:
                    delay = 5 * (2**attempt)
                else:
                    delay = 2**attempt
                time.sleep(min(delay, 30.0))
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt >= self.retries:
                    raise
                time.sleep(min(2**attempt, 30.0))
        raise RuntimeError(f"request failed: {last_error}")

    def get_json(self, url: str) -> dict[str, Any]:
        """Fetch a URL and parse the response body as a JSON object."""
        response = self.get(url)
        try:
            value = json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid JSON response from {url}") from error
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object from {url}")
        return value
