from __future__ import annotations

import io
from urllib.error import HTTPError, URLError

import pytest

from tradingagents.dataflows import provider_http
from tradingagents.dataflows.errors import ProviderResponseError, ProviderTransientError


@pytest.mark.unit
def test_transient_error_retries_with_bounded_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([URLError("private detail"), io.BytesIO(b'{"ok": true}')])
    delays: list[float] = []

    def open_response(*_: object, **__: object) -> io.BytesIO:
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(provider_http, "urlopen", open_response)
    assert provider_http.get_json(
        "https://example.test/data", attempts=2, sleeper=delays.append
    ) == {"ok": True}
    assert delays == [0.25]


@pytest.mark.unit
def test_transient_http_failure_does_not_leak_provider_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "private-provider-detail"

    def reject(*_: object, **__: object) -> None:
        raise HTTPError("https://example.test", 429, secret, {"Retry-After": "100"}, None)

    monkeypatch.setattr(provider_http, "urlopen", reject)
    with pytest.raises(ProviderTransientError) as caught:
        provider_http.get_json("https://example.test/data", attempts=2, sleeper=lambda _: None)
    assert secret not in str(caught.value)


@pytest.mark.unit
def test_response_size_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_http, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"12345"))
    with pytest.raises(ProviderResponseError, match="size limit"):
        provider_http.get_json("https://example.test/data", max_bytes=4)


@pytest.mark.unit
def test_malformed_json_is_a_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_http, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"no"))
    with pytest.raises(ProviderResponseError, match="malformed JSON"):
        provider_http.get_json("https://example.test/data", attempts=1)


@pytest.mark.unit
def test_absolute_deadline_caps_each_transport_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, float] = {}

    def respond(*_: object, **kwargs: object) -> io.BytesIO:
        observed["timeout"] = float(kwargs["timeout"])
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(provider_http.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(provider_http, "urlopen", respond)

    assert provider_http.get_json(
        "https://example.test/data",
        timeout=10.0,
        deadline=101.25,
    ) == {"ok": True}
    assert observed["timeout"] == 1.25


@pytest.mark.unit
def test_expired_deadline_stops_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_http.time, "monotonic", lambda: 100.0)
    transport = pytest.fail
    monkeypatch.setattr(provider_http, "urlopen", transport)

    with pytest.raises(ProviderTransientError, match="deadline"):
        provider_http.get_json("https://example.test/data", deadline=100.0)


@pytest.mark.unit
def test_retry_after_cannot_extend_the_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = {"value": 100.0}
    delays: list[float] = []
    calls = {"count": 0}

    def reject(*_: object, **__: object) -> None:
        calls["count"] += 1
        raise HTTPError(
            "https://example.test",
            429,
            "temporary",
            {"Retry-After": "100"},
            None,
        )

    def sleep(seconds: float) -> None:
        delays.append(seconds)
        now["value"] += seconds

    monkeypatch.setattr(provider_http.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(provider_http, "urlopen", reject)

    with pytest.raises(ProviderTransientError, match="deadline"):
        provider_http.get_json(
            "https://example.test/data",
            attempts=2,
            sleeper=sleep,
            deadline=101.0,
        )

    assert calls["count"] == 1
    assert delays == [1.0]


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    ["https://user@example.test/data", "https://:password@example.test/data"],
)
def test_credentials_in_transport_url_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="without credentials"):
        provider_http.get_json(url)
