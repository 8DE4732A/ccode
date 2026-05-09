from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any

from .config import load_config, mask_secret
from .remote import (
    REMOTE_HUB_PID_PATH,
    REMOTE_SERVER_LOG_BACKUPS,
    REMOTE_SERVER_LOG_MAX_BYTES,
    REMOTE_SERVER_LOG_PATH,
    REMOTE_SESSIONS_PATH,
    _hub_health_url,
    ensure_remote_hub,
    fetch_hub_health,
    remote_host_port,
    remote_url,
    scan_remote_sessions,
)


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{value} B"
        size /= 1024
    return f"{value} B"


def _log_size() -> int:
    try:
        return REMOTE_SERVER_LOG_PATH.stat().st_size
    except FileNotFoundError:
        return 0


def _hub_pid() -> int | None:
    try:
        pid = int(REMOTE_HUB_PID_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def _print_session(session: dict[str, Any]) -> None:
    print(f"  - {session.get('id', '<unknown>')}")
    print(f"    tmux: {session.get('tmux_session', '<unknown>')}")
    print(f"    title: {session.get('title', '<unknown>')}")
    print(f"    cwd: {session.get('cwd', '<unknown>')}")
    print(f"    created: {session.get('created_at', '<unknown>')}")
    print(f"    owner pid: {session.get('owner_pid', '<unknown>')}")
    args = session.get("args")
    if isinstance(args, list) and args:
        print(f"    args: {' '.join(str(arg) for arg in args)}")


def print_status() -> int:
    config = load_config()
    remote = config.get("remote", {})
    running, dead = scan_remote_sessions(prune=True)
    hub_ok, health, error = fetch_hub_health(config, timeout=1)
    pid = _hub_pid()
    host, port = remote_host_port(config)

    print("ccode remote status")
    print()
    print("Config:")
    print(f"  default remote: {'on' if remote.get('enabled') else 'off'}")
    print(f"  url: {remote_url(config, include_token=False)}")
    print(f"  host: {host}")
    print(f"  port: {port}")
    print(f"  prefix: {remote.get('session_name', 'ccode-claude')}")
    print(f"  token: {mask_secret(str(remote.get('token') or ''))}")
    print()
    print("Hub:")
    print(f"  status: {'running' if hub_ok else 'stopped'}")
    print(f"  health url: {_hub_health_url(config)}")
    if pid is not None:
        print(f"  pid: {pid}")
    if health is not None:
        print(f"  name: {health.get('name', '<unknown>')}")
        print(f"  multi session: {health.get('multi_session', False)}")
    if error:
        print(f"  error: {error}")
    print(f"  log: {REMOTE_SERVER_LOG_PATH}")
    print(f"  log size: {_format_bytes(_log_size())}")
    print(f"  log rotation: {_format_bytes(REMOTE_SERVER_LOG_MAX_BYTES)} x {REMOTE_SERVER_LOG_BACKUPS} backups")
    print()
    print("Sessions:")
    print(f"  running: {len(running)}")
    print(f"  registry: {REMOTE_SESSIONS_PATH}")
    if dead:
        print(f"  stale pruned: {len(dead)}")
    for session in running:
        _print_session(session)
    return 0


def start_hub() -> int:
    config = load_config()
    error = ensure_remote_hub(config)
    if error:
        print(error, file=sys.stderr)
        return 1
    print("ccode remote hub is running")
    print(f"url: {remote_url(config, include_token=False)}")
    print(f"health: {_hub_health_url(config)}")
    print(f"log: {REMOTE_SERVER_LOG_PATH}")
    print(f"log rotation: {_format_bytes(REMOTE_SERVER_LOG_MAX_BYTES)} x {REMOTE_SERVER_LOG_BACKUPS} backups")
    return 0


def stop_hub() -> int:
    pid = _hub_pid()
    if pid is None:
        print("ccode remote hub is already stopped")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("ccode remote hub is already stopped")
        return 0
    except PermissionError as exc:
        print(f"failed to stop pid {pid}: {exc}", file=sys.stderr)
        return 1

    deadline = time.time() + 3
    while time.time() < deadline:
        if _hub_pid() is None:
            REMOTE_HUB_PID_PATH.unlink(missing_ok=True)
            print("ccode remote hub stopped")
            return 0
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        print(f"failed to kill pid {pid}: {exc}", file=sys.stderr)
        return 1
    REMOTE_HUB_PID_PATH.unlink(missing_ok=True)
    print("ccode remote hub stopped")
    return 0


def restart_hub() -> int:
    stop_code = stop_hub()
    if stop_code != 0:
        return stop_code
    return start_hub()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "status"
    if len(args) > 1 or command not in {"start", "restart", "stop", "status"}:
        print("Usage: ccode-remote [start|restart|stop|status]", file=sys.stderr)
        raise SystemExit(2)
    if command == "start":
        raise SystemExit(start_hub())
    if command == "restart":
        raise SystemExit(restart_hub())
    if command == "stop":
        raise SystemExit(stop_hub())
    raise SystemExit(print_status())


if __name__ == "__main__":
    main()
