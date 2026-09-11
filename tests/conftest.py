import pytest
from fastapi.testclient import TestClient
from pocker_agent.api import create_app


class MemoryVault:
    """OS credential-store boundary, isolated per test; SQLite remains real."""
    def __init__(self):
        self.values = {}
    def set_password(self, service, user, value):
        self.values[service, user] = value
    def get_password(self, service, user):
        return self.values.get((service, user))
    def delete_password(self, service, user):
        self.values.pop((service, user), None)


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    for name in ("POCKER_AGENT_API_KEY", "OPENAI_API_KEY", "POCKER_AGENT_BASE_URL", "POCKER_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    vault = MemoryVault()
    app = create_app(tmp_path / "test.db", vault)
    return app, TestClient(app), vault
