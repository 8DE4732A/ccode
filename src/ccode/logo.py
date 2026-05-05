from __future__ import annotations

import curses
import math
import random


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


def _put(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
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


def _color_attr(
    line_index: int,
    col_index: int,
    frame: int,
    use_color: bool,
    color_pairs: list[int],
) -> int:
    if not use_color or not color_pairs:
        return 0
    wave = math.sin((frame / 6) + (line_index / 2) + (col_index / 6))
    bias = (line_index + col_index + frame // 2) % len(color_pairs)
    idx = int((wave + 1) * 0.5 * (len(color_pairs) - 1))
    pair_id = color_pairs[(idx + bias) % len(color_pairs)]
    return curses.color_pair(pair_id)


def render_style_wave(
    stdscr: curses.window,
    lines: list[str],
    start_y: int,
    frame: int,
    use_color: bool,
    color_pairs: list[int],
) -> None:
    _, width = stdscr.getmaxyx()
    max_len = max(len(line) for line in lines)
    base_x = max(0, (width - max_len) // 2)
    for row, line in enumerate(lines):
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            wave_y = math.sin((frame / 6) + (col / 8)) * 0.6
            jitter = math.sin((frame / 3) + (row * 1.3 + col / 5)) * 0.4
            draw_y = int(round(start_y + row + wave_y + jitter))
            dx = int(round(math.sin((frame / 8) + row) * 1.5))
            draw_x = base_x + col + dx
            shimmer = ((frame + col + row * 3) % 18 == 0)
            attr = _color_attr(row, col, frame, use_color, color_pairs)
            if shimmer:
                attr |= curses.A_BOLD
            _put(stdscr, draw_y, draw_x, ch, attr)


def render_style_pulse(
    stdscr: curses.window,
    lines: list[str],
    start_y: int,
    frame: int,
    use_color: bool,
    color_pairs: list[int],
) -> None:
    _, width = stdscr.getmaxyx()
    max_len = max(len(line) for line in lines)
    base_x = max(0, (width - max_len) // 2)
    for row, line in enumerate(lines):
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            attr = 0
            if use_color and color_pairs:
                idx = (col + row + frame // 3) % len(color_pairs)
                attr = curses.color_pair(color_pairs[idx])
                if (frame + col + row) % 20 < 10:
                    attr |= curses.A_BOLD
            _put(stdscr, start_y + row, base_x + col, ch, attr)


def render_style_glitch(
    stdscr: curses.window,
    lines: list[str],
    start_y: int,
    frame: int,
    use_color: bool,
    color_pairs: list[int],
) -> None:
    _, width = stdscr.getmaxyx()
    max_len = max(len(line) for line in lines)
    base_x = max(0, (width - max_len) // 2)
    for row, line in enumerate(lines):
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            draw_y = start_y + row
            draw_x = base_x + col
            attr = 0
            if use_color and color_pairs:
                idx = (row + col) % len(color_pairs)
                attr = curses.color_pair(color_pairs[idx])
            if random.random() < 0.03:
                glitch_type = random.randint(0, 2)
                if glitch_type == 0:
                    draw_x += random.randint(-1, 1)
                    draw_y += random.randint(-1, 0)
                elif glitch_type == 1:
                    ch = random.choice("!@#$%&?<>")
                elif glitch_type == 2:
                    attr |= curses.A_REVERSE
            _put(stdscr, draw_y, draw_x, ch, attr)


def render_style_rain(
    stdscr: curses.window,
    lines: list[str],
    start_y: int,
    frame: int,
    use_color: bool,
    color_pairs: list[int],
) -> None:
    _, width = stdscr.getmaxyx()
    max_len = max(len(line) for line in lines)
    base_x = max(0, (width - max_len) // 2)
    for row, line in enumerate(lines):
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            attr = 0
            if use_color and color_pairs:
                idx = (row - (frame // 2)) % len(color_pairs)
                attr = curses.color_pair(color_pairs[idx])
                if (col * 7 + row * 13 + frame) % 17 == 0:
                    attr |= curses.A_BOLD
            _put(stdscr, start_y + row, base_x + col, ch, attr)
