from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import keyring
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .storage import connect, data_path

#: Opt-in for local/private model servers (e.g. Ollama). Off by default so a
#: user-supplied URL cannot be turned into an SSRF probe against the LAN or a
#: cloud metadata endpoint (ADR-0017).
ALLOW_PRIVATE_URLS_ENV = "POCKER_AGENT_ALLOW_PRIVATE_MODEL_URLS"
_BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal",
                      "metadata.google", "instance-data"}


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = ""
    model: str = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))


class BlockedModelHost(ValueError):
    """A model URL points at a non-public address and private use is not allowed."""


def private_model_urls_allowed() -> bool:
    return os.getenv(ALLOW_PRIVATE_URLS_ENV, "").strip().lower() in {
        "1", "true", "yes", "on"}


def _resolved_addresses(hostname: str) -> list[ipaddress._BaseAddress]:
    """Every IP a hostname resolves to, or the literal itself.

    A literal IP is parsed directly so an IPv6 link-local address is checked
    without depending on ``getaddrinfo`` scope handling.
    """
    host = hostname.strip().strip("[]")
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise BlockedModelHost(f"无法解析模型地址：{hostname}") from error
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def _is_blocked_address(address: ipaddress._BaseAddress) -> bool:
    return bool(address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified)


def validate_model_host(hostname: str | None) -> None:
    """Refuse loopback, private, link-local, reserved and metadata addresses.

    The check runs on *every* resolved IPv4/IPv6 address, so a name that returns
    a mix of public and private records is still refused. Redirect hops are
    re-checked by the HTTP transport, not only the originally saved base URL.
    """
    if private_model_urls_allowed():
        return
    if not hostname:
        raise BlockedModelHost("模型地址缺少主机名")
    lowered = hostname.strip().lower().rstrip(".")
    if lowered in _BLOCKED_HOSTNAMES or lowered.endswith(".localhost"):
        raise BlockedModelHost(_PRIVATE_HINT)
    for address in _resolved_addresses(hostname):
        if _is_blocked_address(address):
            raise BlockedModelHost(_PRIVATE_HINT)


_PRIVATE_HINT = ("模型地址指向本机/内网，已拒绝；本机模型请设置 "
                 f"{ALLOW_PRIVATE_URLS_ENV}=1")


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Base URL 必须是 http(s) API 地址，不能包含账号、查询参数或片段")
    validate_model_host(parts.hostname)
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
