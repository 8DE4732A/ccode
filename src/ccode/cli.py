from __future__ import annotations

import curses
import sys

from .ui import CursesApp


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    app = CursesApp(args)
    curses.wrapper(app.run)
