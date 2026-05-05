from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any

from .config import MODEL_KEYS, mask_secret


def _truncate_body(text: str, max_len: int = 200) -> str:
    return f"{text[:max_len]}..." if len(text) > max_len else text


def fetch_models(base_url: str, api_key: str) -> list[dict[str, str]]:
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.getcode()
            body_bytes = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            if exc.fp is not None:
                body = exc.fp.read().decode("utf-8", "replace").strip()
        except Exception:
            body = ""
        message = _truncate_body(body) or "No response body"
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc

    if status != 200:
        body = body_bytes.decode("utf-8", "replace").strip()
        message = _truncate_body(body) or "No response body"
        raise RuntimeError(f"HTTP {status}: {message}")

    try:
        payload = json.loads(body_bytes.decode("utf-8", "replace"))
    except ValueError as exc:
        raise RuntimeError("Invalid JSON response") from exc

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Response JSON missing data array")

    models: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        owned_by = item.get("owned_by")
        if isinstance(model_id, str) and isinstance(owned_by, str):
            models.append({"id": model_id, "owned_by": owned_by})

    return models


def validate_models(config: dict[str, Any], models: list[dict[str, str]]) -> bool:
    valid = {(item["owned_by"], item["id"]) for item in models}
    changed = False
    for key in MODEL_KEYS:
        entry = config["models"].get(key, {})
        owned_by = entry.get("owned_by")
        model_id = entry.get("id")
        if not owned_by or not model_id or (owned_by, model_id) not in valid:
            if entry.get("owned_by") is not None or entry.get("id") is not None:
                config["models"][key] = {"owned_by": None, "id": None}
                changed = True
    return changed


def build_models_by_owner(
    models_data: list[dict[str, str]] | None,
) -> dict[str, list[str]]:
    by_owner: dict[str, list[str]] = {}
    for item in models_data or []:
        owned_by = item.get("owned_by")
        model_id = item.get("id")
        if not isinstance(owned_by, str) or not isinstance(model_id, str):
            continue
        by_owner.setdefault(owned_by, []).append(model_id)
    for owner in by_owner:
        by_owner[owner] = sorted(by_owner[owner])
    return by_owner


def owner_options(models_by_owner: dict[str, list[str]]) -> list[str]:
    return sorted(models_by_owner.keys())


def model_options(models_by_owner: dict[str, list[str]], owner: str) -> list[str]:
    return models_by_owner.get(owner, [])


def build_env(config: dict[str, Any], masked: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = mask_secret(api_key) if masked else api_key
    for key in MODEL_KEYS:
        entry = config["models"].get(key, {})
        model_id = entry.get("id")
        if model_id:
            env[f"ANTHROPIC_DEFAULT_{key.upper()}_MODEL"] = model_id
    for key, value in config.get("toggles", {}).items():
        env[key] = str(value)
    return env


def validate_launch_requirements(config: dict[str, Any]) -> str | None:
    base_url = config.get("base_url", "").strip()
    api_key = config.get("api_key", "").strip()
    if not base_url or not api_key:
        return "Base URL and API key are required."
    for key in MODEL_KEYS:
        entry = config["models"].get(key, {})
        if not entry.get("owned_by") or not entry.get("id"):
            return "OPUS, SONNET, and HAIKU selections are required."
    return None


def launch_claude(config: dict[str, Any], args: list[str]) -> str | None:
    base_url = config.get("base_url", "").strip()
    api_key = config.get("api_key", "").strip()
    if not base_url or not api_key:
        return "Missing base URL or API key."
    env = build_env(config, masked=False)
    try:
        subprocess.run(["claude", *args], check=True, env=env)
    except FileNotFoundError:
        return "Could not find 'claude' on PATH."
    except subprocess.CalledProcessError as exc:
        return f"claude exited with code {exc.returncode}."
    return None
