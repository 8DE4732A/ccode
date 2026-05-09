from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".ccode"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8317"
MODEL_KEYS = ("opus", "sonnet", "haiku")
MODEL_LABELS = {
    "opus": "OPUS",
    "sonnet": "SONNET",
    "haiku": "HAIKU",
}

# toggles 的默认值：值统一用字符串存储
DEFAULT_TOGGLES: dict[str, str] = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "DISABLE_COST_WARNINGS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

DEFAULT_REMOTE: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 8765,
    "token": "",
    "session_name": "ccode-claude",
    "reuse_session": True,
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


def default_config() -> dict[str, Any]:
    return {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
        "models": {
            "opus": {"owned_by": None, "id": None},
            "sonnet": {"owned_by": None, "id": None},
            "haiku": {"owned_by": None, "id": None},
        },
        "toggles": DEFAULT_TOGGLES.copy(),
        "remote": DEFAULT_REMOTE.copy(),
    }


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
                config["models"][key]["owned_by"] = (
                    owned_by if isinstance(owned_by, str) else None
                )
                config["models"][key]["id"] = model_id if isinstance(model_id, str) else None

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

    remote = data.get("remote")
    if isinstance(remote, dict):
        enabled = remote.get("enabled")
        if isinstance(enabled, bool):
            config["remote"]["enabled"] = enabled
        host = remote.get("host")
        if isinstance(host, str):
            config["remote"]["host"] = host
        port = remote.get("port")
        if isinstance(port, int):
            config["remote"]["port"] = port
        token = remote.get("token")
        if isinstance(token, str):
            config["remote"]["token"] = token
        session_name = remote.get("session_name")
        if isinstance(session_name, str):
            config["remote"]["session_name"] = session_name
        reuse_session = remote.get("reuse_session")
        if isinstance(reuse_session, bool):
            config["remote"]["reuse_session"] = reuse_session

    return config


def save_config(config: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def mask_secret(value: str) -> str:
    if not value:
        return "<unset>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def update_model_owner(config: dict[str, Any], key: str, owner: str | None) -> None:
    config["models"][key] = {"owned_by": owner if owner else None, "id": None}
    save_config(config)


def update_model_id(
    config: dict[str, Any], key: str, owner: str | None, model_id: str | None
) -> None:
    if owner and model_id:
        config["models"][key] = {"owned_by": owner, "id": model_id}
    else:
        config["models"][key] = {"owned_by": owner if owner else None, "id": None}
    save_config(config)
