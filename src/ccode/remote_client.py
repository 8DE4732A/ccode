from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

from .config import CONFIG_DIR, load_config
from .remote_common import build_auth_payload, file_lock, http_to_ws_url, missing_extra_message, pid_from_path, remote_server_app_id, remote_server_app_key, remote_server_config, remote_server_url, rotate_log

REMOTE_CLIENT_LOCK_PATH = CONFIG_DIR / "remote_client.lock"
REMOTE_CLIENT_PID_PATH = CONFIG_DIR / "remote_client.pid"
REMOTE_CLIENT_LOG_PATH = CONFIG_DIR / "remote_client.log"
REMOTE_CLIENT_LOG_MAX_BYTES = 5 * 1024 * 1024
REMOTE_CLIENT_LOG_BACKUPS = 3


def _log_line(message: str) -> str:
    from .remote_common import _utc_now

    return f"[{_utc_now()}] {message}"


def _log_event(message: str) -> None:
    print(_log_line(message), flush=True)


def _rotate_log() -> None:
    rotate_log(REMOTE_CLIENT_LOG_PATH, REMOTE_CLIENT_LOG_MAX_BYTES, REMOTE_CLIENT_LOG_BACKUPS)


def ensure_remote_client_connector(config: dict[str, Any]) -> str | None:
    with file_lock(REMOTE_CLIENT_LOCK_PATH):
        if pid_from_path(REMOTE_CLIENT_PID_PATH) is not None:
            return None
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_log()
        log_file = REMOTE_CLIENT_LOG_PATH.open("ab")
        log_file.write((_log_line("starting remote client connector") + "\n").encode())
        log_file.flush()
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "ccode.remote_client", "connect"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            REMOTE_CLIENT_PID_PATH.write_text(str(process.pid))
        except OSError as exc:
            log_file.write((_log_line(f"failed to start connector error={exc}") + "\n").encode())
            log_file.close()
            return f"Failed to start remote client connector: {exc}"
        log_file.close()
        return None


def stop_remote_client_connector() -> str | None:
    try:
        pid = int(REMOTE_CLIENT_PID_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        REMOTE_CLIENT_PID_PATH.unlink(missing_ok=True)
        return None
    except PermissionError as exc:
        return f"failed to stop pid {pid}: {exc}"
    deadline = time.time() + 3
    while time.time() < deadline:
        if pid_from_path(REMOTE_CLIENT_PID_PATH) is None:
            REMOTE_CLIENT_PID_PATH.unlink(missing_ok=True)
            return None
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    REMOTE_CLIENT_PID_PATH.unlink(missing_ok=True)
    return None


def remote_client_pid() -> int | None:
    return pid_from_path(REMOTE_CLIENT_PID_PATH)


def _connect_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    server = remote_server_config(config)
    if server.get("verify_tls", True):
        return {}
    return {"ssl": False}


def _control_url(config: dict[str, Any]) -> str:
    return f"{http_to_ws_url(remote_server_url(config))}/client/ws"


def _attach_url(config: dict[str, Any], attach_id: str) -> str:
    return f"{http_to_ws_url(remote_server_url(config))}/client/attach/{attach_id}"


def _sessions_digest(sessions: list[dict[str, Any]]) -> str:
    compact = [
        {
            "id": session.get("id"),
            "tmux_session": session.get("tmux_session"),
            "updated_at": session.get("updated_at"),
        }
        for session in sessions
    ]
    return json.dumps(compact, sort_keys=True, separators=(",", ":"))


async def send_heartbeats(ws: Any, config: dict[str, Any]) -> None:
    from .remote import scan_remote_sessions

    last_digest = ""
    while True:
        running, _dead = scan_remote_sessions(prune=True)
        digest = _sessions_digest(running)
        payload: dict[str, Any] = {"type": "heartbeat"}
        if digest != last_digest:
            payload["sessions"] = running
            last_digest = digest
        await ws.send(json.dumps(payload))
        await asyncio.sleep(5)


async def attach_local_session_to_websocket(ws: Any, session_id: str) -> None:
    from .remote import TerminalBridge, get_remote_session

    session = get_remote_session(session_id)
    if session is None:
        await ws.send(json.dumps({"type": "status", "message": "session not found"}))
        return
    tmux_session = session.get("tmux_session")
    if not isinstance(tmux_session, str):
        await ws.send(json.dumps({"type": "status", "message": "invalid session"}))
        return

    class Adapter:
        async def send_json(self, payload: dict[str, Any]) -> None:
            await ws.send(json.dumps(payload))

    bridge = TerminalBridge(tmux_session)
    reader: asyncio.Task[None] | None = None
    try:
        bridge.start()
        await ws.send(json.dumps({"type": "status", "message": "connected"}))
        reader = asyncio.create_task(bridge.read_loop(Adapter()))
        async for raw in ws:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "input":
                data = payload.get("data")
                if isinstance(data, str):
                    await bridge.write_input(data)
            elif payload.get("type") == "resize":
                rows = payload.get("rows")
                cols = payload.get("cols")
                if isinstance(rows, int) and isinstance(cols, int):
                    await bridge.resize(rows, cols)
    finally:
        if reader is not None:
            reader.cancel()
        bridge.close()


async def open_attach_socket(attach_id: str, session_id: str, config: dict[str, Any]) -> None:
    try:
        import websockets
    except ImportError:
        _log_event(missing_extra_message("remote-client", "websockets"))
        return
    auth_payload = build_auth_payload(remote_server_app_id(config), remote_server_app_key(config), "client.attach")
    async with websockets.connect(_attach_url(config, attach_id), **_connect_kwargs(config)) as ws:
        await ws.send(json.dumps({"type": "auth", **auth_payload, "attach_id": attach_id}))
        await attach_local_session_to_websocket(ws, session_id)


def _log_attach_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        _log_event(f"attach task failed error={exc!r}")


async def handle_control_message(ws: Any, message: str, config: dict[str, Any]) -> None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return
    if payload.get("type") == "attach_request":
        attach_id = payload.get("attach_id")
        session_id = payload.get("session_id")
        if isinstance(attach_id, str) and isinstance(session_id, str):
            task = asyncio.create_task(open_attach_socket(attach_id, session_id, config))
            task.add_done_callback(_log_attach_task_result)


async def connect_control_loop(config: dict[str, Any]) -> None:
    try:
        import websockets
    except ImportError:
        raise RuntimeError(missing_extra_message("remote-client", "websockets")) from None

    server = remote_server_config(config)
    from .remote import scan_remote_sessions

    running, _dead = scan_remote_sessions(prune=True)
    auth_payload = build_auth_payload(remote_server_app_id(config), remote_server_app_key(config), "client.control")
    async with websockets.connect(_control_url(config), **_connect_kwargs(config)) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            **auth_payload,
            "device_id": server.get("device_id") or "",
            "device_name": server.get("device_name") or "",
            "version": "0.2.0",
            "sessions": running,
        }))
        heartbeat = asyncio.create_task(send_heartbeats(ws, config))
        try:
            async for message in ws:
                await handle_control_message(ws, message, config)
        finally:
            heartbeat.cancel()


async def _run_loop(config: dict[str, Any]) -> None:
    while True:
        try:
            await connect_control_loop(config)
        except Exception as exc:
            _log_event(f"connector disconnected error={exc}")
        await asyncio.sleep(3)


def run_remote_client_forever(config: dict[str, Any]) -> str | None:
    if not remote_server_url(config):
        return "Remote server URL is required."
    if not remote_server_app_id(config).strip() or not remote_server_app_key(config).strip():
        return "Remote server client app ID and app key are required."
    if remote_server_url(config).startswith("http://"):
        _log_event("warning: remote server URL uses plain HTTP/WS")
    try:
        asyncio.run(_run_loop(config))
    except KeyboardInterrupt:
        return None
    return None


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["connect"]:
        error = run_remote_client_forever(load_config())
        if error:
            print(error, file=sys.stderr)
            raise SystemExit(1)
        return
    print("Usage: python -m ccode.remote_client connect", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
