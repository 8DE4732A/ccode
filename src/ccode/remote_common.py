from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
from importlib import resources
from typing import Any, Iterator
from urllib.parse import quote, urlparse

from .config import CONFIG_DIR


def remote_mode(config: dict[str, Any]) -> str:
    mode = str(config.get("remote", {}).get("mode") or "local").strip().lower()
    return mode if mode in {"local", "server"} else "local"


def remote_local_config(config: dict[str, Any]) -> dict[str, Any]:
    remote = config.get("remote", {})
    local = remote.get("local") if isinstance(remote.get("local"), dict) else {}
    return local if isinstance(local, dict) else {}


def remote_server_config(config: dict[str, Any]) -> dict[str, Any]:
    remote = config.get("remote", {})
    server = remote.get("server") if isinstance(remote.get("server"), dict) else {}
    return server if isinstance(server, dict) else {}


def remote_local_host_port(config: dict[str, Any]) -> tuple[str, int]:
    local = remote_local_config(config)
    host = str(local.get("host") or "127.0.0.1")
    try:
        port = int(local.get("port") or 8765)
    except (TypeError, ValueError):
        port = 8765
    return host, port


def remote_local_token(config: dict[str, Any]) -> str:
    return str(remote_local_config(config).get("token") or "")


def remote_local_url(config: dict[str, Any], include_token: bool = False) -> str:
    host, port = remote_local_host_port(config)
    url = f"http://{host}:{port}/"
    token = remote_local_token(config)
    if include_token and token:
        return f"{url}?token={quote(token)}"
    return url


def remote_server_url(config: dict[str, Any]) -> str:
    url = str(remote_server_config(config).get("url") or "").strip().rstrip("/")
    if url and "://" not in url:
        return f"http://{url}"
    return url


def remote_display_url(config: dict[str, Any]) -> str:
    if remote_mode(config) == "server":
        return remote_server_url(config) or "<server unset>"
    return remote_local_url(config, include_token=False)


def http_to_ws_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return "wss://" + parsed.netloc + parsed.path.rstrip("/")
    if parsed.scheme == "http":
        return "ws://" + parsed.netloc + parsed.path.rstrip("/")
    if parsed.scheme in {"ws", "wss"}:
        return url
    return url


def _sanitize_session_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return sanitized or "ccode-claude"


def _new_session_id(config: dict[str, Any]) -> str:
    remote = config.get("remote", {})
    prefix = _sanitize_session_name(str(remote.get("session_name") or "ccode-claude"))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{os.getpid()}"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def token_matches(expected: str, provided: str | None) -> bool:
    value = (provided or "").strip()
    expected = (expected or "").strip()
    return bool(value) and bool(expected) and secrets.compare_digest(value, expected)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def auth_token_matches(expected: str, token: str | None = None, authorization: str | None = None) -> bool:
    return token_matches(expected, token) or token_matches(expected, bearer_token(authorization))


async def websocket_auth_token(websocket: Any) -> str | None:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        payload = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError):
        return None
    if payload.get("type") != "auth":
        return None
    token = payload.get("token")
    return token if isinstance(token, str) else None


_ASSET_TEXT_CACHE: dict[str, str] = {}


def _cached_asset_text(key: str, path: Any) -> str:
    cached = _ASSET_TEXT_CACHE.get(key)
    if cached is None:
        cached = path.read_text(encoding="utf-8")
        _ASSET_TEXT_CACHE[key] = cached
    return cached


def asset_text(name: str) -> str:
    return _cached_asset_text(name, resources.files("ccode.web").joinpath(name))


def web_asset_text(name: str) -> str:
    return _cached_asset_text(f"assets/{name}", resources.files("ccode.web").joinpath("assets", name))


@contextlib.contextmanager
def file_lock(path: Any) -> Iterator[None]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def rotate_log(path: Any, max_bytes: int, backups: int) -> None:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size < max_bytes:
        return
    for index in range(backups, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        target = path.with_name(f"{path.name}.{index}")
        if not source.exists():
            continue
        if index == backups and target.exists():
            target.unlink()
        source.replace(target)


def pid_from_path(path: Any) -> int | None:
    try:
        pid = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def missing_extra_message(extra: str, packages: str) -> str:
    return f"Missing remote dependency. Install with `uv sync --extra {extra}` or `pip install 'ccoding[{extra}]'` ({packages})."
