from __future__ import annotations

import curses
import curses.ascii
import random
import secrets
from typing import Any
from .api import (
    build_models_by_owner,
    fetch_models,
    launch_claude,
    model_options,
    owner_options,
    validate_launch_requirements,
    validate_models,
)
from .config import (
    MODEL_KEYS,
    MODEL_LABELS,
    env_schema_description,
    env_schema_enum,
    env_schema_keys,
    load_config,
    mask_secret,
    save_config,
    update_model_id,
    update_model_owner,
)
from .logo import (
    LOGOS,
    render_style_glitch,
    render_style_pulse,
    render_style_rain,
    render_style_wave,
)
from .remote import remote_url, run_remote_server

# 配置界面 field 类型标识
_FIELD_BASE_URL = "base_url"
_FIELD_API_KEY = "api_key"
_FIELD_REMOTE_ENABLED = "remote.enabled"
_FIELD_REMOTE_HOST = "remote.host"
_FIELD_REMOTE_PORT = "remote.port"
_FIELD_REMOTE_SESSION_NAME = "remote.session_name"
_FIELD_REMOTE_REUSE_SESSION = "remote.reuse_session"
_FIELD_REMOTE_TOKEN = "remote.token"
_FIELD_TOGGLE = "toggle:"      # 前缀 + key
_FIELD_ADD_TOGGLE = "__add__"  # 新增行


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
        self.remote_enabled = bool(self.config.get("remote", {}).get("enabled", False))
        self.models_data: list[dict[str, str]] | None = None
        self.models_by_owner: dict[str, list[str]] = {}
        self.status_message = ""
        self.active_screen = "main"
        self.main_focus_row = 0
        self.main_focus_field = 0
        self.config_focus_index = 0
        # 文本字段光标位置
        remote = self.config.get("remote", {})
        self.config_cursor: dict[str, int] = {
            _FIELD_BASE_URL: len(self.config.get("base_url", "")),
            _FIELD_API_KEY: len(self.config.get("api_key", "")),
            _FIELD_REMOTE_HOST: len(str(remote.get("host", ""))),
            _FIELD_REMOTE_PORT: len(str(remote.get("port", ""))),
            _FIELD_REMOTE_SESSION_NAME: len(str(remote.get("session_name", ""))),
            _FIELD_REMOTE_TOKEN: len(str(remote.get("token", ""))),
        }
        # 新增 toggle 时 key / value 的输入状态
        self._add_phase: int = 0       # 0=未激活, 1=输入key, 2=输入value
        self._add_key: str = ""
        self._add_key_cursor: int = 0
        self._add_value: str = ""
        self._add_value_cursor: int = 0
        # Tab 补全：仅追踪当前选中索引，候选列表按需从 _live_matches() 获取
        self._complete_idx: int = -1

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

    # ------------------------------------------------------------------
    # 动态构建配置界面 field 列表
    # ------------------------------------------------------------------
    def _config_fields(self) -> list[str]:
        toggles = self.config.get("toggles", {})
        fields = [
            _FIELD_BASE_URL,
            _FIELD_API_KEY,
            _FIELD_REMOTE_ENABLED,
            _FIELD_REMOTE_HOST,
            _FIELD_REMOTE_PORT,
            _FIELD_REMOTE_SESSION_NAME,
            _FIELD_REMOTE_REUSE_SESSION,
            _FIELD_REMOTE_TOKEN,
        ]
        for k in toggles:
            fields.append(f"{_FIELD_TOGGLE}{k}")
        fields.append(_FIELD_ADD_TOGGLE)
        return fields

    def _toggle_key_from_field(self, field: str) -> str:
        return field[len(_FIELD_TOGGLE):]

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

    def render_logo(self, stdscr: curses.window, start_y: int) -> None:
        kwargs = dict(
            stdscr=stdscr,
            lines=self.logo_lines,
            start_y=start_y,
            frame=self.frame,
            use_color=self.use_color,
            color_pairs=self.color_pairs,
        )
        if self.render_style == "wave":
            render_style_wave(**kwargs)
        elif self.render_style == "pulse":
            render_style_pulse(**kwargs)
        elif self.render_style == "glitch":
            render_style_glitch(**kwargs)
        elif self.render_style == "rain":
            render_style_rain(**kwargs)
        else:
            render_style_wave(**kwargs)

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

    # ------------------------------------------------------------------
    # Main screen
    # ------------------------------------------------------------------
    def render_main(self, stdscr: curses.window) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        y = 1
        start_y = y
        self.render_logo(stdscr, start_y)
        y = start_y + len(self.logo_lines) + 1
        _height, width = stdscr.getmaxyx()
        rows: list[tuple[str, str, str, int]] = []
        max_row = 0
        for key in MODEL_KEYS:
            label = f"{MODEL_LABELS[key]}:".ljust(8)
            owner = self.config["models"].get(key, {}).get("owned_by") or "owned_by"
            model_id = self.config["models"].get(key, {}).get("id") or "model_id"
            row_width = len(label) + 1 + len(owner) + len(" | ") + len(model_id)
            max_row = max(max_row, row_width)
            rows.append((label, owner, model_id, row_width))
        remote = self.config.get("remote", {})
        remote_line = "REMOTE: on  " + remote_url(self.config) if self.remote_enabled else "REMOTE: off"
        if str(remote.get("host", "")) == "0.0.0.0":
            remote_line += "  [warning: network exposed]"
        hint = "enter to start, r remote on/off, c config, b refresh, a/d change model, q quit"
        content_width = max(max_row, len(remote_line), len(hint), len(self.status_message))
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
        addstr_safe(stdscr, y, x, remote_line)
        y += 1
        addstr_safe(stdscr, y, x, hint)
        y += 1
        if self.status_message:
            addstr_safe(stdscr, y, x, self.status_message)

    # ------------------------------------------------------------------
    # Config screen
    # ------------------------------------------------------------------
    def render_config(self, stdscr: curses.window) -> None:
        _height, width = stdscr.getmaxyx()
        x = max(2, (width - 72) // 2)
        fields = self._config_fields()
        # 防止 focus 越界
        self.config_focus_index = max(0, min(self.config_focus_index, len(fields) - 1))
        focused = fields[self.config_focus_index]

        y = 1
        addstr_safe(stdscr, y, x, "─── Credentials ───────────────────────────────────────────────────────")
        y += 1

        # BASE_URL
        base_url_value = self.config.get("base_url", "")
        base_label = "BASE_URL:"
        base_x = x + len(base_label) + 1
        base_focus = focused == _FIELD_BASE_URL
        base_display = base_url_value if base_focus else (base_url_value or "<unset>")
        addstr_safe(stdscr, y, x, base_label)
        addstr_safe(stdscr, y, base_x, base_display, curses.A_REVERSE if base_focus else 0)
        base_y = y
        y += 1

        # API_KEY
        api_key_value = self.config.get("api_key", "")
        api_label = "API_KEY: "
        api_x = x + len(api_label) + 1
        api_focus = focused == _FIELD_API_KEY
        api_display = api_key_value if api_focus else mask_secret(api_key_value)
        addstr_safe(stdscr, y, x, api_label)
        addstr_safe(stdscr, y, api_x, api_display, curses.A_REVERSE if api_focus else 0)
        api_y = y
        y += 1

        y += 1
        addstr_safe(stdscr, y, x, "─── Remote/Web  [space/enter]=toggle  [g]=generate token ─────────────")
        y += 1
        remote = self.config.setdefault("remote", {})
        remote_rows: dict[str, tuple[int, int, str]] = {}
        remote_items = [
            (_FIELD_REMOTE_ENABLED, "DEFAULT REMOTE:", "on" if remote.get("enabled") else "off"),
            (_FIELD_REMOTE_HOST, "HOST:", str(remote.get("host", ""))),
            (_FIELD_REMOTE_PORT, "PORT:", str(remote.get("port", ""))),
            (_FIELD_REMOTE_SESSION_NAME, "PREFIX:", str(remote.get("session_name", ""))),
            (_FIELD_REMOTE_REUSE_SESSION, "REUSE:", "on" if remote.get("reuse_session") else "off"),
            (_FIELD_REMOTE_TOKEN, "TOKEN:", mask_secret(str(remote.get("token", "")))),
        ]
        for field, label, display in remote_items:
            is_focused = focused == field
            label_x = x
            value_x = x + 18
            if field == _FIELD_REMOTE_TOKEN and is_focused:
                display = str(remote.get("token", ""))
            addstr_safe(stdscr, y, label_x, label)
            addstr_safe(stdscr, y, value_x, display or "<unset>", curses.A_REVERSE if is_focused else 0)
            if field == _FIELD_REMOTE_HOST and str(remote.get("host", "")) == "0.0.0.0":
                addstr_safe(stdscr, y, value_x + len(display or "<unset>") + 2, "warning: exposes local terminal")
            remote_rows[field] = (y, value_x, display)
            y += 1

        y += 1
        addstr_safe(stdscr, y, x, "─── Toggles (env vars)  [n]=new  [d]=delete  [ESC]=save & back ────────")
        y += 1

        toggles = self.config.get("toggles", {})
        toggle_rows: dict[str, int] = {}  # key -> screen y
        for tk, tv in toggles.items():
            tf = f"{_FIELD_TOGGLE}{tk}"
            is_focused = focused == tf
            key_disp = tk.ljust(48)
            val_disp = str(tv)
            desc = env_schema_description(tk)
            line = f"  {key_disp} = {val_disp}"
            addstr_safe(stdscr, y, x, line, curses.A_REVERSE if is_focused else 0)
            if is_focused and desc:
                hint_y = y + 1
                addstr_safe(stdscr, hint_y, x + 2, f"[{desc[:66]}]")
            toggle_rows[tk] = y
            y += 1
            if is_focused and desc:
                y += 1  # 占用提示行

        # Add toggle 行
        add_focused = focused == _FIELD_ADD_TOGGLE
        if self._add_phase == 0:
            add_label = "  [ + Add toggle ]"
            addstr_safe(stdscr, y, x, add_label, curses.A_REVERSE if add_focused else 0)
            add_y = y
            y += 1
        else:
            # phase 1: 输入 key
            if self._add_phase == 1:
                prefix_label = "  KEY:   "
                pk_x = x + len(prefix_label)
                addstr_safe(stdscr, y, x, prefix_label)
                addstr_safe(stdscr, y, pk_x, self._add_key or "", curses.A_REVERSE)
                add_y = y
                y += 1
                # 实时候选列表
                matches = self._live_matches()
                if matches:
                    shown = matches[:8]
                    for i, m in enumerate(shown):
                        is_sel = (i == self._complete_idx)
                        addstr_safe(stdscr, y, x + 4, m, curses.A_REVERSE if is_sel else 0)
                        y += 1
                    if len(matches) > 8:
                        addstr_safe(stdscr, y, x + 4, f"  ... (+{len(matches)-8} more)")
                        y += 1
                    addstr_safe(stdscr, y, x + 2, "[Tab] select  [Enter] confirm")
                    y += 1
                else:
                    addstr_safe(stdscr, y, x + 2, "[Tab] autocomplete from 217 known env vars")
                    y += 1
                # schema description hint（精确匹配时显示）
                sel_key = matches[self._complete_idx] if matches and 0 <= self._complete_idx < len(matches) else self._add_key
                desc = env_schema_description(sel_key)
                if desc:
                    addstr_safe(stdscr, y, x + 2, f"[{desc[:68]}]")
                    y += 1
            # phase 2: 输入 value
            else:
                kl = "  KEY:   "
                pk_x = x + len(kl)
                addstr_safe(stdscr, y, x, kl + self._add_key)
                y += 1
                enums = env_schema_enum(self._add_key)
                if enums:
                    addstr_safe(stdscr, y, x + 2, "values: " + "  ".join(enums))
                    y += 1
                vl = "  VALUE: "
                pv_x = x + len(vl)
                addstr_safe(stdscr, y, x, vl)
                addstr_safe(stdscr, y, pv_x, self._add_value or "", curses.A_REVERSE)
                add_y = y
                y += 1

        y += 1
        hint_line = "↑↓ navigate  [d] delete toggle  [n] new toggle  [ESC] save & back"
        addstr_safe(stdscr, y, x, hint_line)
        y += 1
        if self.status_message:
            addstr_safe(stdscr, y, x, self.status_message)

        # 光标放置
        if focused == _FIELD_BASE_URL:
            cursor_pos = min(self.config_cursor.get(_FIELD_BASE_URL, 0), len(base_url_value))
            self.place_cursor(stdscr, base_y, base_x + cursor_pos)
        elif focused == _FIELD_API_KEY:
            cursor_pos = min(self.config_cursor.get(_FIELD_API_KEY, 0), len(api_key_value))
            self.place_cursor(stdscr, api_y, api_x + cursor_pos)
        elif focused in (
            _FIELD_REMOTE_HOST,
            _FIELD_REMOTE_PORT,
            _FIELD_REMOTE_SESSION_NAME,
            _FIELD_REMOTE_TOKEN,
        ):
            row_y, row_x, display = remote_rows[focused]
            cursor_pos = min(self.config_cursor.get(focused, 0), len(display))
            self.place_cursor(stdscr, row_y, row_x + cursor_pos)
        elif focused == _FIELD_ADD_TOGGLE and self._add_phase == 1:
            self.place_cursor(stdscr, add_y, x + len("  KEY:   ") + self._add_key_cursor)
        elif focused == _FIELD_ADD_TOGGLE and self._add_phase == 2:
            self.place_cursor(stdscr, add_y, x + len("  VALUE: ") + self._add_value_cursor)
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def place_cursor(self, stdscr: curses.window, y: int, x: int) -> None:
        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height:
            return
        x = max(0, min(x, width - 1))
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            stdscr.move(y, x)
        except curses.error:
            return

    # ------------------------------------------------------------------
    # Config key handling
    # ------------------------------------------------------------------
    def handle_config_key(self, key: int) -> None:
        fields = self._config_fields()
        focused = fields[self.config_focus_index]

        # ESC: 保存返回
        if key == 27:
            self._cancel_add()
            save_config(self.config)
            self.active_screen = "main"
            self.refresh_models()
            return

        # 处于新增输入模式时，单独路由
        if focused == _FIELD_ADD_TOGGLE and self._add_phase > 0:
            self._handle_add_input(key)
            return

        # 上下导航
        if key == curses.KEY_UP:
            save_config(self.config)
            self._cancel_add()
            self.config_focus_index = max(0, self.config_focus_index - 1)
            return
        if key == curses.KEY_DOWN:
            save_config(self.config)
            self._cancel_add()
            self.config_focus_index = min(len(fields) - 1, self.config_focus_index + 1)
            return

        if focused in (_FIELD_REMOTE_ENABLED, _FIELD_REMOTE_REUSE_SESSION):
            if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
                remote = self.config.setdefault("remote", {})
                remote_key = "enabled" if focused == _FIELD_REMOTE_ENABLED else "reuse_session"
                remote[remote_key] = not bool(remote.get(remote_key, False))
                save_config(self.config)
            return

        if focused == _FIELD_REMOTE_TOKEN and key in (ord("g"), ord("G")):
            token = secrets.token_urlsafe(24)
            self.config.setdefault("remote", {})["token"] = token
            self.config_cursor[_FIELD_REMOTE_TOKEN] = len(token)
            save_config(self.config)
            self.status_message = "Remote token regenerated."
            return

        # 文本字段
        if focused in (_FIELD_BASE_URL, _FIELD_API_KEY):
            self._handle_text_field(focused, key)
            return
        if focused in (
            _FIELD_REMOTE_HOST,
            _FIELD_REMOTE_PORT,
            _FIELD_REMOTE_SESSION_NAME,
            _FIELD_REMOTE_TOKEN,
        ):
            self._handle_remote_text_field(focused, key)
            return

        # Toggle 行
        if focused.startswith(_FIELD_TOGGLE):
            tk = self._toggle_key_from_field(focused)
            # d / Delete: 删除
            if key in (ord("d"), ord("D"), curses.KEY_DC):
                toggles = self.config.get("toggles", {})
                if tk in toggles:
                    del toggles[tk]
                    save_config(self.config)
                    # focus 上移一行避免越界
                    self.config_focus_index = max(0, self.config_focus_index - 1)
                return
            # Enter/Space: 开始编辑 value
            if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
                self._start_edit_toggle_value(tk)
                return
            return

        # Add toggle 行
        if focused == _FIELD_ADD_TOGGLE:
            if key in (ord("n"), ord("N"), curses.KEY_ENTER, 10, 13):
                self._add_phase = 1
                self._add_key = ""
                self._add_key_cursor = 0
                self._complete_idx = -1
                return

        # 任意位置按 n 跳到新增行
        if key in (ord("n"), ord("N")):
            add_idx = fields.index(_FIELD_ADD_TOGGLE)
            self.config_focus_index = add_idx
            self._add_phase = 1
            self._add_key = ""
            self._add_key_cursor = 0
            self._complete_idx = -1

    def _handle_text_field(self, field: str, key: int) -> None:
        """处理 base_url / api_key 文本输入。"""
        cfg_key = field  # base_url 或 api_key
        value = self.config.get(cfg_key, "")
        cursor = min(self.config_cursor.get(field, 0), len(value))
        value, cursor = self._edit_string(value, cursor, key)
        self.config[cfg_key] = value
        self.config_cursor[field] = cursor

    def _handle_remote_text_field(self, field: str, key: int) -> None:
        remote = self.config.setdefault("remote", {})
        remote_key = field.removeprefix("remote.")
        value = str(remote.get(remote_key, ""))
        cursor = min(self.config_cursor.get(field, 0), len(value))
        new_value, cursor = self._edit_string(value, cursor, key)
        if field == _FIELD_REMOTE_PORT:
            if new_value.isdigit():
                remote[remote_key] = int(new_value)
                self.status_message = ""
            else:
                self.status_message = "Remote port must be an integer."
                return
        else:
            remote[remote_key] = new_value
        self.config_cursor[field] = cursor

    def _edit_string(self, value: str, cursor: int, key: int) -> tuple[str, int]:
        if key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_RIGHT:
            cursor = min(len(value), cursor + 1)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if cursor > 0:
                value = value[:cursor - 1] + value[cursor:]
                cursor -= 1
        elif 0 <= key <= 255 and curses.ascii.isprint(key):
            value = value[:cursor] + chr(key) + value[cursor:]
            cursor += 1
        return value, cursor

    def _start_edit_toggle_value(self, tk: str) -> None:
        """激活 add 流程的 phase 2 来就地编辑已有 toggle 的值（复用输入框）。"""
        # 用 add 流程的 phase 2 复用，但不走 _FIELD_ADD_TOGGLE，而是直接内联
        # 简化实现：直接把现有值放进 add_value 做内联编辑，保存后替换
        current_val = str(self.config.get("toggles", {}).get(tk, ""))
        self._add_key = tk
        self._add_value = current_val
        self._add_value_cursor = len(current_val)
        self._add_phase = 2
        # 把 focus 移到 add toggle 行
        fields = self._config_fields()
        self.config_focus_index = fields.index(_FIELD_ADD_TOGGLE)

    def _handle_add_input(self, key: int) -> None:
        """处理新增/编辑 toggle 时的键盘输入。"""
        if self._add_phase == 1:
            self._handle_add_key_input(key)
        elif self._add_phase == 2:
            self._handle_add_value_input(key)

    def _handle_add_key_input(self, key: int) -> None:
        """phase 1: 输入环境变量名，Tab 触发补全。"""
        if key == 27:  # ESC 取消
            self._cancel_add()
            return
        if key == ord("\t"):  # Tab 补全
            self._do_complete()
            return
        if key in (curses.KEY_ENTER, 10, 13):
            k = self._add_key.strip()
            if k:
                self._add_phase = 2
                # 预填已知 enum 的第一个值
                enums = env_schema_enum(k)
                self._add_value = enums[0] if enums else ""
                self._add_value_cursor = len(self._add_value)
            return
        # 普通文本编辑
        cursor = self._add_key_cursor
        value = self._add_key
        cursor = min(cursor, len(value))
        if key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_RIGHT:
            cursor = min(len(value), cursor + 1)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if cursor > 0:
                value = value[:cursor - 1] + value[cursor:]
                cursor -= 1
        elif 0 <= key <= 255 and curses.ascii.isprint(key):
            ch = chr(key).upper()
            value = value[:cursor] + ch + value[cursor:]
            cursor += 1
        self._add_key = value
        self._add_key_cursor = cursor
        # 输入变化后重置选中索引
        matches = self._live_matches()
        self._complete_idx = 0 if matches else -1

    def _handle_add_value_input(self, key: int) -> None:
        """phase 2: 输入环境变量值，Enter 确认保存。"""
        if key == 27:  # ESC 取消
            self._cancel_add()
            return
        if key in (curses.KEY_ENTER, 10, 13):
            k = self._add_key.strip()
            v = self._add_value
            if k:
                toggles = self.config.setdefault("toggles", {})
                toggles[k] = v
                save_config(self.config)
                self.status_message = f"Saved {k}={v}"
            self._cancel_add()
            return
        # Tab: 在 enum 候选值间循环
        if key == ord("\t"):
            enums = env_schema_enum(self._add_key)
            if enums:
                try:
                    idx = enums.index(self._add_value)
                    self._add_value = enums[(idx + 1) % len(enums)]
                except ValueError:
                    self._add_value = enums[0]
                self._add_value_cursor = len(self._add_value)
            return
        # 普通文本编辑
        cursor = self._add_value_cursor
        value = self._add_value
        cursor = min(cursor, len(value))
        if key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_RIGHT:
            cursor = min(len(value), cursor + 1)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if cursor > 0:
                value = value[:cursor - 1] + value[cursor:]
                cursor -= 1
        elif 0 <= key <= 255 and curses.ascii.isprint(key):
            value = value[:cursor] + chr(key) + value[cursor:]
            cursor += 1
        self._add_value = value
        self._add_value_cursor = cursor

    def _live_matches(self) -> list[str]:
        """根据当前输入前缀实时计算候选列表。"""
        prefix = self._add_key.upper()
        all_keys = env_schema_keys()
        existing = list(self.config.get("toggles", {}).keys())
        candidates = sorted(set(all_keys) | set(existing))
        if not prefix:
            return candidates
        return [k for k in candidates if k.startswith(prefix)]

    def _do_complete(self) -> None:
        """Tab：在候选列表中选中当前项并填入输入框，再按移到下一项。"""
        matches = self._live_matches()
        if not matches:
            return
        if self._complete_idx < 0 or self._complete_idx >= len(matches):
            self._complete_idx = 0
        else:
            self._complete_idx = (self._complete_idx + 1) % len(matches)
        self._add_key = matches[self._complete_idx]
        self._add_key_cursor = len(self._add_key)

    def _cancel_add(self) -> None:
        self._add_phase = 0
        self._add_key = ""
        self._add_key_cursor = 0
        self._add_value = ""
        self._add_value_cursor = 0
        self._complete_idx = -1

    # ------------------------------------------------------------------
    # Main screen key handling
    # ------------------------------------------------------------------
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
        if key in (ord("r"), ord("R")):
            self.remote_enabled = not self.remote_enabled
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
            if self.remote_enabled:
                error = self.launch_remote_with_curses(stdscr)
            else:
                error = self.launch_with_curses(stdscr)
            if error:
                self.status_message = error
            else:
                self.should_exit = True

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

    def _run_outside_curses(self, stdscr: curses.window, launcher: Any) -> str | None:
        try:
            curses.def_prog_mode()
            curses.endwin()
            return launcher(self.config, self.args)
        finally:
            curses.reset_prog_mode()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            stdscr.keypad(True)
            stdscr.clear()
            stdscr.refresh()

    def launch_with_curses(self, stdscr: curses.window) -> str | None:
        return self._run_outside_curses(stdscr, launch_claude)

    def launch_remote_with_curses(self, stdscr: curses.window) -> str | None:
        return self._run_outside_curses(stdscr, run_remote_server)
