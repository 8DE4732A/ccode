#!/usr/bin/env python3
from __future__ import annotations

import curses
import curses.ascii
import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

CONFIG_DIR = Path.home() / ".ccode"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8317"
MODEL_KEYS = ("opus", "sonnet", "haiku")
MODEL_LABELS = {
    "opus": "OPUS",
    "sonnet": "SONNET",
    "haiku": "HAIKU",
}
TOGGLE_LABELS = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "ENABLE TELEMETRY",
    "DISABLE_COST_WARNINGS": "DISABLE COST WARNINGS",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "DISABLE NONESSENTIAL TRAFFIC",
}
DEFAULT_TOGGLES = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": 0,
    "DISABLE_COST_WARNINGS": 1,
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": 1,
}


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
        for key in DEFAULT_TOGGLES:
            value = toggles.get(key)
            if isinstance(value, bool):
                config["toggles"][key] = int(value)
            elif isinstance(value, int) and value in (0, 1):
                config["toggles"][key] = value

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
        if len(body) > 200:
            body = f"{body[:200]}..."
        message = body or "No response body"
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc

    if status != 200:
        body = body_bytes.decode("utf-8", "replace").strip()
        if len(body) > 200:
            body = f"{body[:200]}..."
        message = body or "No response body"
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


LOGOS = [
    # 1. Block (Original)
    [
        " #####   #####   #####   ####   ##### ",
        "##   ## ##   ## ##   ## ##  ## ##   ##",
        "##      ##      ##   ## ##  ## ##     ",
        "##   ## ##   ## ##   ## ##  ## ##   ##",
        " #####   #####   #####   ####   ##### ",
        "    C C O D E   L A U N C H E R        ",
    ],
    # 2. Slant
    [
        "   ______ ______ ____  ____  ______ ",
        "  / ____// ____// __ \\/ __ \\/ ____/ ",
        " / /    / /    / / / / / / / __/    ",
        "/ /___ / /___ / /_/ / /_/ / /___    ",
        "\\____/ \\____/ \\____/_____/_____/    ",
        "   LAUNCHER  EDITION                ",
    ],
    # 3. Thin / Cyber
    [
        "  ___  ___  ___  ___  ___ ",
        " / __|/ __|/ _ \\|   \\| __|",
        "| (__| (__| (_) | |) | _| ",
        " \\___|\\___|\\___/|___/|___|",
        "   C C O D E L A U N C H  ",
    ],
]


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


def addstr_safe(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    if x >= width:
        return
    available = width - x
    if available <= 0:
        return
    try:
        stdscr.addstr(y, x, text[:available], attr)
    except curses.error:
        return


class CursesApp:
    def __init__(self, args: list[str]) -> None:
        self.args = args
        self.config = load_config()
        self.models_data: list[dict[str, str]] | None = None
        self.models_by_owner: dict[str, list[str]] = {}
        self.status_message = ""
        self.active_screen = "main"
        self.main_focus_row = 0
        self.main_focus_field = 0
        self.config_fields = ["base_url", "api_key", *TOGGLE_LABELS.keys()]
        self.config_focus_index = 0
        self.config_cursor = {
            "base_url": len(self.config.get("base_url", "")),
            "api_key": len(self.config.get("api_key", "")),
        }
        self.should_exit = False
        self.frame = 0
        self.use_color = False
        self.color_pairs: list[int] = []
        self.logo_palette = [
            (curses.COLOR_CYAN, curses.COLOR_BLUE),
            (curses.COLOR_BLUE, curses.COLOR_MAGENTA),
            (curses.COLOR_MAGENTA, curses.COLOR_RED),
            (curses.COLOR_RED, curses.COLOR_YELLOW),
            (curses.COLOR_YELLOW, curses.COLOR_GREEN),
        ]
        self.logo_lines = random.choice(LOGOS)
        self.render_style = random.choice(["wave", "pulse", "glitch", "rain"])
        self.update_models_by_owner()

    def update_models_by_owner(self) -> None:
        self.models_by_owner = build_models_by_owner(self.models_data)

    def fetch_and_store_models(
        self, base_url: str, api_key: str, save: bool = True
    ) -> str | None:
        try:
            models = fetch_models(base_url, api_key)
        except RuntimeError as exc:
            return str(exc)
        self.models_data = models
        self.config["base_url"] = base_url
        self.config["api_key"] = api_key
        changed = validate_models(self.config, models)
        if save or changed:
            save_config(self.config)
        self.update_models_by_owner()
        return None

    def refresh_models(self) -> None:
        self.status_message = ""
        base_url = self.config.get("base_url", "").strip()
        api_key = self.config.get("api_key", "").strip()
        if not base_url or not api_key:
            self.status_message = "Base URL and API key are required. Open config with c."
            return
        error = self.fetch_and_store_models(base_url, api_key)
        if error:
            self.status_message = error
            return
        self.status_message = "Models refreshed."

    def init_colors(self) -> None:
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            return
        self.use_color = True
        self.color_pairs = []
        pair_id = 1
        for fg, bg in self.logo_palette:
            try:
                curses.init_pair(pair_id, fg, bg)
            except curses.error:
                break
            self.color_pairs.append(pair_id)
            pair_id += 1

    def logo_color_attr(self, line_index: int, col_index: int) -> int:
        if not self.use_color or not self.color_pairs:
            return 0
        wave = math.sin((self.frame / 6) + (line_index / 2) + (col_index / 6))
        bias = (line_index + col_index + self.frame // 2) % len(self.color_pairs)
        idx = int((wave + 1) * 0.5 * (len(self.color_pairs) - 1))
        pair_id = self.color_pairs[(idx + bias) % len(self.color_pairs)]
        return curses.color_pair(pair_id)

    def render_logo(self, stdscr: curses.window, start_y: int) -> None:
        if self.render_style == "wave":
            self.render_style_wave(stdscr, start_y)
        elif self.render_style == "pulse":
            self.render_style_pulse(stdscr, start_y)
        elif self.render_style == "glitch":
            self.render_style_glitch(stdscr, start_y)
        elif self.render_style == "rain":
            self.render_style_rain(stdscr, start_y)
        else:
            self.render_style_wave(stdscr, start_y)

    def render_style_wave(self, stdscr: curses.window, start_y: int) -> None:
        lines = self.logo_lines
        height, width = stdscr.getmaxyx()
        max_len = max(len(line) for line in lines)
        base_x = max(0, (width - max_len) // 2)
        for row, line in enumerate(lines):
            for col, ch in enumerate(line):
                if ch == " ":
                    continue
                wave_y = math.sin((self.frame / 6) + (col / 8)) * 0.6
                jitter = math.sin((self.frame / 3) + (row * 1.3 + col / 5)) * 0.4
                draw_y = int(round(start_y + row + wave_y + jitter))
                dx = int(round(math.sin((self.frame / 8) + row) * 1.5))
                draw_x = base_x + col + dx
                shimmer = ((self.frame + col + row * 3) % 18 == 0)
                attr = self.logo_color_attr(row, col)
                if shimmer:
                    attr |= curses.A_BOLD
                addstr_safe(stdscr, draw_y, draw_x, ch, attr)

    def render_style_pulse(self, stdscr: curses.window, start_y: int) -> None:
        lines = self.logo_lines
        height, width = stdscr.getmaxyx()
        max_len = max(len(line) for line in lines)
        base_x = max(0, (width - max_len) // 2)

        for row, line in enumerate(lines):
            for col, ch in enumerate(line):
                if ch == " ":
                    continue

                attr = 0
                if self.use_color and self.color_pairs:
                    # Gradient pulse
                    idx = (col + row + self.frame // 3) % len(self.color_pairs)
                    attr = curses.color_pair(self.color_pairs[idx])
                    # Gentle shimmer
                    if (self.frame + col + row) % 20 < 10:
                        attr |= curses.A_BOLD

                addstr_safe(stdscr, start_y + row, base_x + col, ch, attr)

    def render_style_glitch(self, stdscr: curses.window, start_y: int) -> None:
        lines = self.logo_lines
        height, width = stdscr.getmaxyx()
        max_len = max(len(line) for line in lines)
        base_x = max(0, (width - max_len) // 2)

        for row, line in enumerate(lines):
            for col, ch in enumerate(line):
                if ch == " ":
                    continue

                draw_y = start_y + row
                draw_x = base_x + col
                attr = 0

                if self.use_color and self.color_pairs:
                    idx = (row + col) % len(self.color_pairs)
                    attr = curses.color_pair(self.color_pairs[idx])

                # Glitch effect: random chance to modify drawing
                if random.random() < 0.03:
                    glitch_type = random.randint(0, 2)
                    if glitch_type == 0:
                        # Jitter position
                        draw_x += random.randint(-1, 1)
                        draw_y += random.randint(-1, 0) # Only up/same
                    elif glitch_type == 1:
                        # Change character
                        ch = random.choice("!@#$%&?<>")
                    elif glitch_type == 2:
                        attr |= curses.A_REVERSE

                addstr_safe(stdscr, draw_y, draw_x, ch, attr)

    def render_style_rain(self, stdscr: curses.window, start_y: int) -> None:
        lines = self.logo_lines
        height, width = stdscr.getmaxyx()
        max_len = max(len(line) for line in lines)
        base_x = max(0, (width - max_len) // 2)

        for row, line in enumerate(lines):
            for col, ch in enumerate(line):
                if ch == " ":
                    continue

                attr = 0
                if self.use_color and self.color_pairs:
                    # Vertical flow falling down
                    idx = (row - (self.frame // 2)) % len(self.color_pairs)
                    attr = curses.color_pair(self.color_pairs[idx])

                    # Sparkles
                    if (col * 7 + row * 13 + self.frame) % 17 == 0:
                         attr |= curses.A_BOLD

                addstr_safe(stdscr, start_y + row, base_x + col, ch, attr)

    def run(self, stdscr: curses.window) -> None:
        stdscr.keypad(True)
        curses.noecho()
        curses.cbreak()
        stdscr.timeout(50)
        self.init_colors()
        if self.active_screen == "main":
            self.refresh_models()
        while not self.should_exit:
            stdscr.erase()
            if self.active_screen == "main":
                self.render_main(stdscr)
            else:
                self.render_config(stdscr)
            stdscr.refresh()
            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                continue
            if key == -1:
                self.frame += 1
                continue
            if self.active_screen == "main":
                self.handle_main_key(stdscr, key)
            else:
                self.handle_config_key(key)
            self.frame += 1

    def render_main(self, stdscr: curses.window) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        y = 1
        start_y = y
        self.render_logo(stdscr, start_y)
        y = start_y + len(self.logo_lines) + 1
        height, width = stdscr.getmaxyx()
        rows: list[tuple[str, str, str, int]] = []
        max_row = 0
        for key in MODEL_KEYS:
            label = f"{MODEL_LABELS[key]}:".ljust(8)
            owner = self.config["models"].get(key, {}).get("owned_by") or "owned_by"
            model_id = self.config["models"].get(key, {}).get("id") or "model_id"
            row_width = len(label) + 1 + len(owner) + len(" | ") + len(model_id)
            max_row = max(max_row, row_width)
            rows.append((label, owner, model_id, row_width))
        hint = "enter to start, c to config, b to refresh, a/d to change owned_by/model_id, q to quit"
        content_width = max(max_row, len(hint), len(self.status_message))
        x = max(2, (width - content_width) // 2)
        for idx, (label, owner, model_id, _row_width) in enumerate(rows):
            addstr_safe(stdscr, y, x, label)
            owner_x = x + len(label) + 1
            owner_attr = (
                curses.A_REVERSE
                if (idx == self.main_focus_row and self.main_focus_field == 0)
                else 0
            )
            addstr_safe(stdscr, y, owner_x, owner, owner_attr)
            sep = " | "
            sep_x = owner_x + len(owner)
            addstr_safe(stdscr, y, sep_x, sep)
            model_x = sep_x + len(sep)
            model_attr = (
                curses.A_REVERSE
                if (idx == self.main_focus_row and self.main_focus_field == 1)
                else 0
            )
            addstr_safe(stdscr, y, model_x, model_id, model_attr)
            y += 1
        y += 1
        addstr_safe(stdscr, y, x, hint)
        y += 1
        if self.status_message:
            addstr_safe(stdscr, y, x, self.status_message)

    def render_config(self, stdscr: curses.window) -> None:
        y = 1
        height, width = stdscr.getmaxyx()
        x = max(2, (width - 60) // 2)
        addstr_safe(stdscr, y, x, "-----")
        y += 1
        addstr_safe(stdscr, y, x, "Credentials")
        y += 1
        addstr_safe(stdscr, y, x, "-----")
        y += 1

        base_url_value = self.config.get("base_url", "")
        base_label = "BASE_URL:"
        base_x = x + len(base_label) + 1
        base_focus = self.config_fields[self.config_focus_index] == "base_url"
        base_display = base_url_value if base_focus else (base_url_value or "<unset>")
        addstr_safe(stdscr, y, x, base_label)
        addstr_safe(stdscr, y, base_x, base_display, curses.A_REVERSE if base_focus else 0)
        base_y = y
        y += 1

        api_key_value = self.config.get("api_key", "")
        api_label = "API_KEY:"
        api_x = x + len(api_label) + 1
        api_focus = self.config_fields[self.config_focus_index] == "api_key"
        api_display = api_key_value if api_focus else mask_secret(api_key_value)
        addstr_safe(stdscr, y, x, api_label)
        addstr_safe(stdscr, y, api_x, api_display, curses.A_REVERSE if api_focus else 0)
        api_y = y
        y += 1

        y += 1
        addstr_safe(stdscr, y, x, "-----")
        y += 1
        addstr_safe(stdscr, y, x, "Toggles")
        y += 1
        addstr_safe(stdscr, y, x, "-----")
        y += 1

        toggle_positions: dict[str, tuple[int, int]] = {}
        for key in TOGGLE_LABELS:
            label = f"{TOGGLE_LABELS[key]}:"
            value = "ON" if self.config.get("toggles", {}).get(key, 0) else "OFF"
            toggle_focus = self.config_fields[self.config_focus_index] == key
            addstr_safe(stdscr, y, x, label)
            value_x = x + len(label) + 1
            addstr_safe(stdscr, y, value_x, value, curses.A_REVERSE if toggle_focus else 0)
            toggle_positions[key] = (y, value_x)
            y += 1

        y += 1
        if self.status_message:
            addstr_safe(stdscr, y, x, self.status_message)

        cursor_field = self.config_fields[self.config_focus_index]
        if cursor_field == "base_url":
            cursor_pos = min(self.config_cursor.get("base_url", 0), len(base_url_value))
            self.place_cursor(stdscr, base_y, base_x + cursor_pos)
        elif cursor_field == "api_key":
            cursor_pos = min(self.config_cursor.get("api_key", 0), len(api_key_value))
            self.place_cursor(stdscr, api_y, api_x + cursor_pos)
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def place_cursor(self, stdscr: curses.window, y: int, x: int) -> None:
        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height:
            return
        if x < 0:
            x = 0
        if x >= width:
            x = width - 1
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            stdscr.move(y, x)
        except curses.error:
            return

    def handle_main_key(self, stdscr: curses.window, key: int) -> None:
        if key == curses.KEY_UP:
            self.main_focus_row = max(0, self.main_focus_row - 1)
            return
        if key == curses.KEY_DOWN:
            self.main_focus_row = min(len(MODEL_KEYS) - 1, self.main_focus_row + 1)
            return
        if key == curses.KEY_LEFT:
            self.main_focus_field = 0
            return
        if key == curses.KEY_RIGHT:
            self.main_focus_field = 1
            return
        if key in (ord("["), ord("a")):
            self.cycle_main_option(-1)
            return
        if key in (ord("]"), ord("d")):
            self.cycle_main_option(1)
            return
        if key in (ord("c"), ord("C")):
            self.active_screen = "config"
            self.status_message = ""
            return
        if key in (ord("b"), ord("B")):
            self.refresh_models()
            return
        if key in (ord("q"), ord("Q")):
            self.should_exit = True
            return
        if key in (curses.KEY_ENTER, 10, 13):
            self.status_message = ""
            error = validate_launch_requirements(self.config)
            if error:
                self.status_message = error
                return
            error = self.launch_with_curses(stdscr)
            if error:
                self.status_message = error
            else:
                self.should_exit = True

    def handle_config_key(self, key: int) -> None:
        if key == 27:
            save_config(self.config)
            self.active_screen = "main"
            self.refresh_models()
            return
        if key == curses.KEY_UP:
            self.config_focus_index = max(0, self.config_focus_index - 1)
            return
        if key == curses.KEY_DOWN:
            self.config_focus_index = min(
                len(self.config_fields) - 1, self.config_focus_index + 1
            )
            return

        field = self.config_fields[self.config_focus_index]
        if field in ("base_url", "api_key"):
            self.handle_text_input(field, key)
            return
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            current = self.config.get("toggles", {}).get(field, 0)
            self.config["toggles"][field] = 0 if current else 1
            save_config(self.config)

    def handle_text_input(self, field: str, key: int) -> None:
        value = self.config.get(field, "")
        cursor = min(self.config_cursor.get(field, 0), len(value))
        if key == curses.KEY_LEFT:
            if cursor > 0:
                cursor -= 1
        elif key == curses.KEY_RIGHT:
            if cursor < len(value):
                cursor += 1
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if cursor > 0:
                value = value[: cursor - 1] + value[cursor:]
                cursor -= 1
                self.config[field] = value
                save_config(self.config)
        elif 0 <= key <= 255 and curses.ascii.isprint(key):
            value = value[:cursor] + chr(key) + value[cursor:]
            cursor += 1
            self.config[field] = value
            save_config(self.config)
        self.config_cursor[field] = cursor

    def cycle_main_option(self, direction: int) -> None:
        key = MODEL_KEYS[self.main_focus_row]
        if self.main_focus_field == 0:
            owners = owner_options(self.models_by_owner)
            if not owners:
                return
            current = self.config["models"].get(key, {}).get("owned_by")
            if current in owners:
                index = owners.index(current)
            else:
                index = -1 if direction > 0 else 0
            new_owner = owners[(index + direction) % len(owners)]
            update_model_owner(self.config, key, new_owner)
            return

        owner = self.config["models"].get(key, {}).get("owned_by")
        if not owner:
            return
        models = model_options(self.models_by_owner, owner)
        if not models:
            return
        current_id = self.config["models"].get(key, {}).get("id")
        if current_id in models:
            index = models.index(current_id)
        else:
            index = -1 if direction > 0 else 0
        new_id = models[(index + direction) % len(models)]
        update_model_id(self.config, key, owner, new_id)

    def launch_with_curses(self, stdscr: curses.window) -> str | None:
        try:
            curses.def_prog_mode()
            curses.endwin()
            return launch_claude(self.config, self.args)
        finally:
            curses.reset_prog_mode()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            stdscr.keypad(True)
            stdscr.clear()
            stdscr.refresh()


def main() -> None:
    app = CursesApp(sys.argv[1:])
    curses.wrapper(app.run)


if __name__ == "__main__":
    main()
