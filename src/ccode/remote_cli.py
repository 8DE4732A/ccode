from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .config import load_config, mask_secret
from .remote_common import pid_from_path, remote_display_url, remote_local_app_id, remote_local_app_key, remote_local_host_port, remote_mode, remote_server_config, remote_server_url


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{value} B"
        size /= 1024
    return f"{value} B"


def _path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


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


def _stop_pid(pid_path: Path, label: str) -> int:
    pid = pid_from_path(pid_path)
    if pid is None:
        print(f"{label} is already stopped")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"{label} is already stopped")
        return 0
    except PermissionError as exc:
        print(f"failed to stop pid {pid}: {exc}", file=sys.stderr)
        return 1
    deadline = time.time() + 3
    while time.time() < deadline:
        if pid_from_path(pid_path) is None:
            pid_path.unlink(missing_ok=True)
            print(f"{label} stopped")
            return 0
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        print(f"failed to kill pid {pid}: {exc}", file=sys.stderr)
        return 1
    pid_path.unlink(missing_ok=True)
    print(f"{label} stopped")
    return 0


def print_status(kind: str | None = None) -> int:
    config = load_config()
    mode = remote_mode(config)
    effective = kind or ("client" if mode == "server" else "local")
    from .remote import REMOTE_SESSIONS_PATH, scan_remote_sessions

    running, dead = scan_remote_sessions(prune=True)
    print("ccode remote status")
    print()
    print("Config:")
    print(f"  mode: {mode}")
    print(f"  default remote: {'on' if config.get('remote', {}).get('enabled') else 'off'}")
    print(f"  url: {remote_display_url(config)}")
    print(f"  prefix: {config.get('remote', {}).get('session_name', 'ccode-claude')}")
    if mode == "server":
        server = remote_server_config(config)
        print(f"  device name: {server.get('device_name') or '<unset>'}")
        print(f"  device id: {server.get('device_id') or '<unset>'}")
        print(f"  client app id: {server.get('appId') or '<unset>'}")
        print(f"  client app key: {mask_secret(str(server.get('appKey') or ''))}")
    else:
        host, port = remote_local_host_port(config)
        print(f"  host: {host}")
        print(f"  port: {port}")
        print(f"  app id: {remote_local_app_id(config) or '<unset>'}")
        print(f"  app key: {mask_secret(remote_local_app_key(config))}")
    print()

    if effective == "client":
        from .remote_client import REMOTE_CLIENT_LOG_BACKUPS, REMOTE_CLIENT_LOG_MAX_BYTES, REMOTE_CLIENT_LOG_PATH, REMOTE_CLIENT_PID_PATH

        pid = pid_from_path(REMOTE_CLIENT_PID_PATH)
        print("Client connector:")
        print(f"  status: {'running' if pid is not None else 'stopped'}")
        if pid is not None:
            print(f"  pid: {pid}")
        print(f"  server: {remote_server_url(config) or '<unset>'}")
        print(f"  log: {REMOTE_CLIENT_LOG_PATH}")
        print(f"  log size: {_format_bytes(_path_size(REMOTE_CLIENT_LOG_PATH))}")
        print(f"  log rotation: {_format_bytes(REMOTE_CLIENT_LOG_MAX_BYTES)} x {REMOTE_CLIENT_LOG_BACKUPS} backups")
    else:
        from .remote import REMOTE_HUB_PID_PATH, REMOTE_SERVER_LOG_BACKUPS, REMOTE_SERVER_LOG_MAX_BYTES, REMOTE_SERVER_LOG_PATH, _hub_health_url, fetch_hub_health

        hub_ok, health, error = fetch_hub_health(config, timeout=1)
        pid = pid_from_path(REMOTE_HUB_PID_PATH)
        print("Local hub:")
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
        print(f"  log size: {_format_bytes(_path_size(REMOTE_SERVER_LOG_PATH))}")
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


def start_local() -> int:
    from .remote import REMOTE_SERVER_LOG_BACKUPS, REMOTE_SERVER_LOG_MAX_BYTES, REMOTE_SERVER_LOG_PATH, _hub_health_url, ensure_remote_hub, remote_url

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


def stop_local() -> int:
    from .remote import REMOTE_HUB_PID_PATH

    return _stop_pid(REMOTE_HUB_PID_PATH, "ccode remote hub")


def restart_local() -> int:
    stop_code = stop_local()
    if stop_code != 0:
        return stop_code
    return start_local()


def start_client() -> int:
    from .remote_client import REMOTE_CLIENT_LOG_PATH, ensure_remote_client_connector

    config = load_config()
    error = ensure_remote_client_connector(config)
    if error:
        print(error, file=sys.stderr)
        return 1
    print("ccode remote client connector is running")
    print(f"server: {remote_server_url(config) or '<unset>'}")
    print(f"log: {REMOTE_CLIENT_LOG_PATH}")
    return 0


def stop_client() -> int:
    from .remote_client import REMOTE_CLIENT_PID_PATH

    return _stop_pid(REMOTE_CLIENT_PID_PATH, "ccode remote client connector")


def restart_client() -> int:
    stop_code = stop_client()
    if stop_code != 0:
        return stop_code
    return start_client()


def _dispatch(target: str, command: str) -> int:
    if target == "local":
        actions = {"status": lambda: print_status("local"), "start": start_local, "stop": stop_local, "restart": restart_local}
    else:
        actions = {"status": lambda: print_status("client"), "start": start_client, "stop": stop_client, "restart": restart_client}
    return actions[command]()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    targets = {"local", "client"}
    commands = {"start", "restart", "stop", "status"}
    if not args:
        config = load_config()
        raise SystemExit(_dispatch("client" if remote_mode(config) == "server" else "local", "status"))
    if args[0] in targets:
        if len(args) > 2 or (len(args) == 2 and args[1] not in commands):
            print("Usage: ccode-remote [local|client] [start|restart|stop|status]", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(_dispatch(args[0], args[1] if len(args) == 2 else "status"))
    if len(args) == 1 and args[0] in commands:
        config = load_config()
        raise SystemExit(_dispatch("client" if remote_mode(config) == "server" else "local", args[0]))
    print("Usage: ccode-remote [start|restart|stop|status] or ccode-remote [local|client] [start|restart|stop|status]", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
