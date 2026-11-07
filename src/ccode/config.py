from __future__ import annotations

import copy
import json
import secrets
import socket
import uuid
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".ccode"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8317"
# 模型 key 及其对应的环境变量，dict 顺序即主界面展示顺序。
# auto 对应客户端 auto mode 分类器模型，其余沿用 ANTHROPIC_DEFAULT_<KEY>_MODEL。
MODEL_ENV_VARS = {
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "auto": "CLAUDE_CODE_AUTO_MODE_MODEL",
}
MODEL_KEYS = tuple(MODEL_ENV_VARS)
MODEL_LABELS = {key: key.upper() for key in MODEL_KEYS}

# auto 为可选项，其余模型启动时必选
OPTIONAL_MODEL_KEYS = ("auto",)
REQUIRED_MODEL_KEYS = tuple(key for key in MODEL_KEYS if key not in OPTIONAL_MODEL_KEYS)

# toggles 的默认值：值统一用字符串存储
DEFAULT_TOGGLES: dict[str, str] = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "DISABLE_COST_WARNINGS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

DEFAULT_REMOTE: dict[str, Any] = {
    "enabled": False,
    "mode": "local",
    "session_name": "ccode-claude",
    "reuse_session": True,
    "local": {
        "host": "127.0.0.1",
        "port": 8765,
        "appId": "",
        "appKey": "",
    },
    "server": {
        "url": "",
        "appId": "",
        "appKey": "",
        "device_name": "",
        "device_id": "",
        "auto_connect": True,
        "verify_tls": True,
    },
}

_ENV_SCHEMA: dict[str, Any] = {}
_SCHEMA_KEYS: list[str] = []


def load_env_schema() -> dict[str, Any]:
    """加载 env_schema.json，返回 properties 字典，key 为变量名，value 含 description 等。"""
    global _ENV_SCHEMA
    if not _ENV_SCHEMA:
        schema_path = Path(__file__).with_name("env_schema.json")
        try:
            _ENV_SCHEMA = json.loads(schema_path.read_text())
        except (OSError, json.JSONDecodeError):
            _ENV_SCHEMA = {}
    return _ENV_SCHEMA.get("properties", {})


def env_schema_keys() -> list[str]:
    """返回所有已知环境变量名（有序）。"""
    global _SCHEMA_KEYS
    if not _SCHEMA_KEYS:
        _SCHEMA_KEYS = sorted(load_env_schema().keys())
    return _SCHEMA_KEYS


def env_schema_description(key: str) -> str:
    """返回某个变量的 description，不存在返回空字符串。"""
    return load_env_schema().get(key, {}).get("description", "")


def env_schema_enum(key: str) -> list[str]:
    """返回某个变量的允许值列表，不存在返回空列表。"""
    return load_env_schema().get(key, {}).get("enum", [])


def model_entry(
    owner: str | None = None,
    model_id: str | None = None,
    one_m: bool = False,
) -> dict[str, Any]:
    """模型条目的唯一构造入口，新增字段时只需改这里。"""
    return {"owned_by": owner, "id": model_id, "1m": one_m}


def default_config() -> dict[str, Any]:
    return {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
        "models": {key: model_entry() for key in MODEL_KEYS},
        "toggles": DEFAULT_TOGGLES.copy(),
        "skip_permissions": False,
        "remote": copy.deepcopy(DEFAULT_REMOTE),
    }


def _device_name_default() -> str:
    try:
        return socket.gethostname() or "ccode-device"
    except OSError:
        return "ccode-device"


def _new_device_id() -> str:
    return f"ccode-{uuid.uuid4().hex}"


def _new_app_id() -> str:
    return f"app_{secrets.token_urlsafe(9)}"


def normalize_remote(remote: Any) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_REMOTE)
    if not isinstance(remote, dict):
        return normalized

    enabled = remote.get("enabled")
    if isinstance(enabled, bool):
        normalized["enabled"] = enabled
    mode = remote.get("mode")
    if isinstance(mode, str) and mode in {"local", "server"}:
        normalized["mode"] = mode
    session_name = remote.get("session_name")
    if isinstance(session_name, str):
        normalized["session_name"] = session_name
    reuse_session = remote.get("reuse_session")
    if isinstance(reuse_session, bool):
        normalized["reuse_session"] = reuse_session

    local = remote.get("local") if isinstance(remote.get("local"), dict) else {}
    host = local.get("host") if isinstance(local, dict) else None
    if not isinstance(host, str):
        host = remote.get("host")
    if isinstance(host, str):
        normalized["local"]["host"] = host
    port = local.get("port") if isinstance(local, dict) else None
    if not isinstance(port, int):
        port = remote.get("port")
    if isinstance(port, int):
        normalized["local"]["port"] = port
    local_app_id = local.get("appId") if isinstance(local, dict) else None
    local_app_key = local.get("appKey") if isinstance(local, dict) else None
    local_token = local.get("token") if isinstance(local, dict) else None
    if not isinstance(local_token, str):
        local_token = remote.get("token")
    if isinstance(local_app_id, str):
        normalized["local"]["appId"] = local_app_id
    elif isinstance(local_token, str) and local_token:
        normalized["local"]["appId"] = _new_app_id()
    if isinstance(local_app_key, str):
        normalized["local"]["appKey"] = local_app_key
    elif isinstance(local_token, str):
        normalized["local"]["appKey"] = local_token

    server = remote.get("server") if isinstance(remote.get("server"), dict) else {}
    if isinstance(server, dict):
        server_token = server.get("token")
        for key in ("url", "appId", "appKey", "device_name", "device_id"):
            value = server.get(key)
            if isinstance(value, str):
                normalized["server"][key] = value
        if not normalized["server"].get("appId") and isinstance(server_token, str) and server_token:
            normalized["server"]["appId"] = _new_app_id()
        if not normalized["server"].get("appKey") and isinstance(server_token, str):
            normalized["server"]["appKey"] = server_token
        for key in ("auto_connect", "verify_tls"):
            value = server.get(key)
            if isinstance(value, bool):
                normalized["server"][key] = value

    if normalized["mode"] == "server":
        if not normalized["server"].get("device_id"):
            normalized["server"]["device_id"] = _new_device_id()
        if not normalized["server"].get("device_name"):
            normalized["server"]["device_name"] = _device_name_default()
    return normalized


def load_config() -> dict[str, Any]:
    config = default_config()
    try:
        raw = CONFIG_PATH.read_text()
        data = json.loads(raw)
    except FileNotFoundError:
        return config
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(data, dict):
        return config

    base_url = data.get("base_url")
    if isinstance(base_url, str):
        config["base_url"] = base_url
    api_key = data.get("api_key")
    if isinstance(api_key, str):
        config["api_key"] = api_key

    models = data.get("models")
    if isinstance(models, dict):
        for key in MODEL_KEYS:
            entry = models.get(key)
            if isinstance(entry, dict):
                owned_by = entry.get("owned_by")
                model_id = entry.get("id")
                one_m = entry.get("1m")
                config["models"][key] = model_entry(
                    owned_by if isinstance(owned_by, str) else None,
                    model_id if isinstance(model_id, str) else None,
                    one_m if isinstance(one_m, bool) else False,
                )

    toggles = data.get("toggles")
    if isinstance(toggles, dict):
        # 读取全部 key（包括用户自定义的），值统一转为字符串
        new_toggles: dict[str, str] = {}
        for k, v in toggles.items():
            if isinstance(k, str):
                if isinstance(v, bool):
                    new_toggles[k] = "1" if v else "0"
                elif isinstance(v, int):
                    new_toggles[k] = str(v)
                elif isinstance(v, str):
                    new_toggles[k] = v
        if new_toggles:
            config["toggles"] = new_toggles

    skip_permissions = data.get("skip_permissions")
    if isinstance(skip_permissions, bool):
        config["skip_permissions"] = skip_permissions

    config["remote"] = normalize_remote(data.get("remote"))

    return config


def save_config(config: dict[str, Any]) -> None:
    config["remote"] = normalize_remote(config.get("remote"))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def mask_secret(value: str) -> str:
    if not value:
        return "<unset>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _model_entry(
    config: dict[str, Any],
    key: str,
    owner: str | None,
    model_id: str | None,
) -> dict[str, Any]:
    """重建模型条目，保留已有的 1M 上下文开关状态。"""
    existing = config["models"].get(key, {})
    return model_entry(owner if owner else None, model_id, bool(existing.get("1m")))


def update_model_owner(config: dict[str, Any], key: str, owner: str | None) -> None:
    config["models"][key] = _model_entry(config, key, owner, None)
    save_config(config)


def update_model_id(
    config: dict[str, Any], key: str, owner: str | None, model_id: str | None
) -> None:
    config["models"][key] = _model_entry(config, key, owner, model_id)
    save_config(config)


def update_model_1m(config: dict[str, Any], key: str, enabled: bool) -> None:
    config["models"][key]["1m"] = bool(enabled)
    save_config(config)
