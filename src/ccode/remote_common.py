from __future__ import annotations

import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from importlib import resources
from typing import Any, Iterator
from urllib.parse import urlencode, urlparse

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


def remote_local_app_id(config: dict[str, Any]) -> str:
    return str(remote_local_config(config).get("appId") or "")


def remote_local_app_key(config: dict[str, Any]) -> str:
    return str(remote_local_config(config).get("appKey") or "")


def remote_server_app_id(config: dict[str, Any]) -> str:
    return str(remote_server_config(config).get("appId") or "")


def remote_server_app_key(config: dict[str, Any]) -> str:
    return str(remote_server_config(config).get("appKey") or "")


def remote_local_url(config: dict[str, Any], include_token: bool = False) -> str:
    host, port = remote_local_host_port(config)
    return f"http://{host}:{port}/"


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


AUTH_MAX_AGE_SECONDS = 24 * 60 * 60
AUTH_FUTURE_SKEW_SECONDS = 5 * 60


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    error: str = ""
    message: str = ""


AUTH_OK = AuthResult(True)


def new_app_id() -> str:
    return f"app_{secrets.token_urlsafe(9)}"


def new_app_key() -> str:
    return secrets.token_urlsafe(32)


def _auth_message(app_id: str, timestamp: str, scope: str) -> bytes:
    return f"v1\nappId={app_id}\ntimestamp={timestamp}\nscope={scope}".encode()


def make_auth_signature(app_key: str, app_id: str, timestamp: str, scope: str) -> str:
    digest = hmac.new(app_key.encode(), _auth_message(app_id, timestamp, scope), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_auth_payload(app_id: str, app_key: str, scope: str, timestamp: int | None = None) -> dict[str, str]:
    timestamp_value = str(int(time.time() if timestamp is None else timestamp))
    return {
        "appId": app_id,
        "timestamp": timestamp_value,
        "sign": make_auth_signature(app_key, app_id, timestamp_value, scope),
    }


def auth_headers(payload: dict[str, str]) -> dict[str, str]:
    return {
        "X-CCODE-App-Id": payload.get("appId", ""),
        "X-CCODE-Timestamp": payload.get("timestamp", ""),
        "X-CCODE-Sign": payload.get("sign", ""),
    }


def auth_query(payload: dict[str, str]) -> str:
    return urlencode({"appId": payload.get("appId", ""), "timestamp": payload.get("timestamp", ""), "sign": payload.get("sign", "")})


def verify_auth_payload(expected_app_id: str, expected_app_key: str, payload: dict[str, Any], scope: str) -> AuthResult:
    app_id = str(payload.get("appId") or "").strip()
    timestamp = str(payload.get("timestamp") or "").strip()
    sign = str(payload.get("sign") or "").strip()
    expected_app_id = (expected_app_id or "").strip()
    expected_app_key = (expected_app_key or "").strip()
    if not expected_app_id or not expected_app_key or not app_id or not timestamp or not sign:
        return AuthResult(False, "auth_required", "Authentication required.")
    if not hmac.compare_digest(expected_app_id, app_id):
        return AuthResult(False, "auth_failed", "Authentication failed.")
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return AuthResult(False, "auth_required", "Invalid authentication timestamp.")
    age = int(time.time()) - timestamp_int
    if age > AUTH_MAX_AGE_SECONDS:
        return AuthResult(False, "reauth_required", "Authentication expired, please re-authenticate.")
    if age < -AUTH_FUTURE_SKEW_SECONDS:
        return AuthResult(False, "auth_failed", "Authentication timestamp is invalid.")
    expected_sign = make_auth_signature(expected_app_key, app_id, timestamp, scope)
    if not hmac.compare_digest(expected_sign, sign):
        return AuthResult(False, "auth_failed", "Authentication failed.")
    return AUTH_OK


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
