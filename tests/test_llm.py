import json
import httpx2
import pytest
from openai import OpenAI
from pocker_agent.llm import OpenAICompatibleClient


def client_for(monkeypatch, handler):
    adapter = OpenAICompatibleClient("test-key", "custom-model", "https://relay.test/v1")
    monkeypatch.setattr(adapter, "_sdk", lambda: OpenAI(api_key=adapter.api_key, base_url=adapter.base_url,
        max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    return adapter


def test_sdk_sends_selected_model_bearer_and_json_contract(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "custom-model"
        assert "temperature" not in body
        assert body["response_format"] == {"type":"json_object"}
        return httpx2.Response(200,json={"choices":[{"message":{"content":'{"ok":true}'}}]})
    client = client_for(monkeypatch, handler)
    assert client.complete([{"role":"user","content":"json please"}],response_format={"type":"json_object"}) == '{"ok":true}'


@pytest.mark.parametrize("body",[
    {"data":[{"id":"z"},{"id":"a"},{"id":"z"}]},
    {"models":["z",{"name":"a"},{"model":"z"}]},
    ["z","a"],
])
def test_model_lists_are_complete_and_deduplicated(monkeypatch, body):
    client = client_for(monkeypatch,lambda request:httpx2.Response(200,json=body))
    assert client.list_models() == ["a","z"]


@pytest.mark.parametrize("code,body",[(401,{"error":"invalid test-key"}),(400,{"error":"bad request"}),(200,{"error":"wrong envelope"})])
def test_errors_are_visible_and_key_is_redacted(monkeypatch, code, body):
    client = client_for(monkeypatch,lambda request:httpx2.Response(code,json=body))
    with pytest.raises(RuntimeError) as caught:
        client.list_models()
    assert "test-key" not in str(caught.value)


def test_html_completion_does_not_become_uncaught_500(monkeypatch):
    client = client_for(monkeypatch,lambda request:httpx2.Response(200,text="<html>not an API</html>"))
    with pytest.raises(RuntimeError,match="无效响应"):
        client.complete([{"role":"user","content":"hello"}])


def test_long_echoed_credential_is_redacted_before_error_is_shortened(monkeypatch):
    client = client_for(monkeypatch, lambda request: httpx2.Response(401, json={"error": client.api_key}))
    client.api_key = "sensitive-" * 100
    with pytest.raises(RuntimeError) as caught:
        client.list_models()
    assert "sensitive" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
