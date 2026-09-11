from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import keyring
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .storage import connect, data_path


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = ""
    model: str = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Base URL 必须是 http(s) API 地址，不能包含账号、查询参数或片段")
    path = parts.path.rstrip("/")
    for endpoint in ("/chat/completions", "/models"):
        if path.endswith(endpoint):
            path = path[:-len(endpoint)]
    # Bare relay domains use /v1; explicit paths such as /api/v2 are preserved.
    return urlunsplit((parts.scheme, parts.netloc.lower(), path or "/v1", "", ""))


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    def public(self) -> dict:
        return {"configured": bool(self.api_key and self.base_url and self.model), "has_key": bool(self.api_key), "base_url": self.base_url, "model": self.model}


class ConfigStore:
    def __init__(self, path: Path | None = None, vault=None):
        self.path = path or data_path()
        self.vault = vault or keyring
        self.lock = threading.RLock()
        self.service = "PockerAgent:" + str(self.path.resolve())
        with connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")

    def read(self) -> ModelConfig:
        with self.lock, connect(self.path) as db:
            row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
            if row:
                data = json.loads(row[0])
                try:
                    secret = self.vault.get_password(self.service, data["key_ref"])
                except keyring.errors.KeyringError as error:
                    raise RuntimeError("系统凭据库读取失败，请重新保存 API Key") from error
                return ModelConfig(data["base_url"], data["model"], secret or "")
            secret = os.getenv("POCKER_AGENT_API_KEY", os.getenv("OPENAI_API_KEY", ""))
            url = os.getenv("POCKER_AGENT_BASE_URL", "")
            return ModelConfig(normalize_url(url) if url else "", os.getenv("POCKER_AGENT_MODEL", ""), secret)

    def draft(self, payload: ConfigInput, *, require_model=True) -> ModelConfig:
        saved = self.read()
        base_url = normalize_url(payload.base_url or saved.base_url)
        secret = payload.api_key.get_secret_value().strip()
        if secret.lower().startswith("bearer "):
            secret = secret[7:].strip()
        if not secret:
            # Never forward a saved provider's credential to a newly typed host/path.
            if base_url != saved.base_url:
                raise ValueError("更换 API 地址时请填写对应的 API Key")
            secret = saved.api_key
        if not secret or any(ord(char) < 33 or ord(char) > 126 for char in secret):
            raise ValueError("请填写有效的 API Key")
        model = payload.model.strip() if "model" in payload.model_fields_set else saved.model
        if require_model and not model:
            raise ValueError("请选择或填写模型名称")
        return ModelConfig(base_url, model, secret)

    def save(self, payload: ConfigInput) -> dict:
        with self.lock:
            candidate = self.draft(payload)
            ref = uuid.uuid4().hex
            try:
                self.vault.set_password(self.service, ref, candidate.api_key)
                with connect(self.path) as db:
                    old = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
                    db.execute("INSERT OR REPLACE INTO configuration VALUES (1, ?)", (json.dumps({"base_url": candidate.base_url, "model": candidate.model, "key_ref": ref}),))
            except (keyring.errors.KeyringError, OSError) as error:
                raise RuntimeError("配置未保存：本机存储或系统凭据库不可用") from error
            # The old credential is retired only after the new configuration commits.
            warning = None
            if old:
                try:
                    self.vault.delete_password(self.service, json.loads(old[0])["key_ref"])
                except keyring.errors.KeyringError:
                    warning = "配置已保存，但旧凭据清理失败"
            return {**candidate.public(), "warning": warning}
