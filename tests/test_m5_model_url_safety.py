"""M5-3: model-URL safety -- no SSRF to loopback, LAN, link-local or metadata.

A user-supplied base URL reaches the network through the OpenAI SDK's httpx
client. This file pins the guard that runs before any such request, including on
every redirect hop, and the opt-in that keeps local model servers usable on a
development machine (ADR-0017).
"""
from __future__ import annotations

import httpx
import pytest

from pocker_agent.configuration import (
    ALLOW_PRIVATE_URLS_ENV,
    BlockedModelHost,
    normalize_url,
    private_model_urls_allowed,
    validate_model_host,
)
from pocker_agent.llm import _guard_model_request

#: A public address literal, so the allowed-path tests need no DNS.
PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def _deny_private(monkeypatch):
    monkeypatch.delenv(ALLOW_PRIVATE_URLS_ENV, raising=False)


@pytest.mark.parametrize("host", [
    "localhost",
    "127.0.0.1",
    "10.0.0.1",
    "192.168.1.5",
    "172.16.0.1",
    "169.254.169.254",
    "::1",
    "fe80::1",
    "metadata.google.internal",
])
def test_private_and_metadata_hosts_are_refused(host):
    with pytest.raises(BlockedModelHost):
        validate_model_host(host)


def test_a_public_literal_is_allowed():
    validate_model_host(PUBLIC)


def test_normalize_url_refuses_a_loopback_base_url():
    with pytest.raises(BlockedModelHost):
        normalize_url("http://127.0.0.1:11434/v1")


@pytest.mark.parametrize("url", [
    "https://user:secret@relay.test/v1",
    "https://relay.test/v1?token=1",
    "https://relay.test/v1#fragment",
])
def test_url_credentials_query_and_fragment_are_still_refused(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_the_env_switch_allows_a_private_model(monkeypatch):
    monkeypatch.setenv(ALLOW_PRIVATE_URLS_ENV, "1")
    assert private_model_urls_allowed()
    validate_model_host("127.0.0.1")
    assert normalize_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"


def test_a_redirect_to_a_private_host_is_revalidated():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == PUBLIC:
            return httpx.Response(307, headers={
                "location": "http://169.254.169.254/latest/meta-data"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler),
                          event_hooks={"request": [_guard_model_request]},
                          follow_redirects=True)
    with pytest.raises(BlockedModelHost):
        client.get(f"http://{PUBLIC}/v1/models")
