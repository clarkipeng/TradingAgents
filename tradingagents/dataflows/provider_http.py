"""Small, bounded JSON transport shared by public-data adapters."""

from __future__ import annotations

import http.client
import json
import math
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from tradingagents.dataflows.errors import ProviderResponseError, ProviderTransientError

_DEFAULT_MAX_BYTES = 1_000_000
_MAX_ATTEMPTS = 3
_USER_AGENT = "TradingAgents public-data collector"


def get_json(
    url: str,
    *,
    timeout: float = 10.0,
    attempts: int = 2,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    sleeper: Callable[[float], None] | None = None,
    deadline: float | None = None,
) -> Any:
    """GET and decode JSON with bounded reads, retries, and wall-clock work."""

    _validate_request(
        url,
        timeout=timeout,
        attempts=attempts,
        max_bytes=max_bytes,
        deadline=deadline,
    )
    sleep = sleeper or time.sleep

    for attempt in range(attempts):
        request_timeout = float(timeout)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderTransientError("Provider request deadline expired")
            request_timeout = min(request_timeout, remaining)
        try:
            request = Request(
                url,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
                method="GET",
            )
            with urlopen(request, timeout=request_timeout) as response:
                payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ProviderResponseError("Provider response exceeded the size limit")
            try:
                return json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise ProviderResponseError("Provider returned malformed JSON") from None
        except HTTPError as exc:
            if not _is_transient_status(exc.code):
                raise ProviderResponseError("Provider returned a rejected HTTP response") from None
            if attempt + 1 == attempts:
                raise ProviderTransientError("Provider remained temporarily unavailable") from None
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            _sleep_before_retry(
                sleep,
                _retry_delay(attempt, retry_after),
                deadline=deadline,
            )
        except (URLError, TimeoutError, OSError, http.client.HTTPException):
            if attempt + 1 == attempts:
                raise ProviderTransientError("Provider request failed transiently") from None
            _sleep_before_retry(sleep, _retry_delay(attempt), deadline=deadline)

    raise AssertionError("unreachable")


def _validate_request(
    url: str,
    *,
    timeout: float,
    attempts: int,
    max_bytes: int,
    deadline: float | None,
) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("url must be a public HTTP(S) URL without credentials")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if timeout <= 0 or not math.isfinite(float(timeout)):
        raise ValueError("timeout must be a positive finite number")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= _MAX_ATTEMPTS
    ):
        raise ValueError(f"attempts must be between 1 and {_MAX_ATTEMPTS}")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if deadline is not None and (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("deadline must be a finite monotonic timestamp")


def _sleep_before_retry(
    sleeper: Callable[[float], None],
    delay: float,
    *,
    deadline: float | None,
) -> None:
    if deadline is None:
        sleeper(delay)
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderTransientError("Provider request deadline expired")
    sleeper(min(delay, remaining))


def _is_transient_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    delay = min(0.25 * (2**attempt), 2.0)
    if retry_after is None:
        return delay
    try:
        requested = float(retry_after)
    except (TypeError, ValueError):
        return delay
    if not math.isfinite(requested) or requested < 0:
        return delay
    return min(max(delay, requested), 5.0)
