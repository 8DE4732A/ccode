import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Iterator
from urllib.request import urlopen

from .api import build_claude_command, build_env
from .config import CONFIG_DIR, CONFIG_PATH, load_config
from .remote_common import (
    _new_session_id,
    _sanitize_session_name,
    _utc_now,
    AuthResult,
    asset_text,
    file_lock,
    missing_extra_message,
    remote_local_app_id,
    remote_local_app_key,
    remote_local_host_port,
    remote_local_url,
    remote_mode,
    remote_server_config,
    remote_server_url,
    rotate_log,
    verify_auth_payload,
    web_asset_text,
)

REMOTE_SESSIONS_PATH = CONFIG_DIR / "remote_sessions.json"
REMOTE_SESSIONS_LOCK_PATH = CONFIG_DIR / "remote_sessions.lock"
REMOTE_HUB_LOCK_PATH = CONFIG_DIR / "remote_hub.lock"
REMOTE_HUB_PID_PATH = CONFIG_DIR / "remote_hub.pid"
REMOTE_SERVER_LOG_PATH = CONFIG_DIR / "remote_server.log"
REMOTE_SERVER_LOG_MAX_BYTES = 5 * 1024 * 1024
REMOTE_SERVER_LOG_BACKUPS = 3
REMOTE_HUB_NAME = "ccode-remote"


class TerminalBridge:
    def __init__(self, session_name: str, rows: int = 32, cols: int = 120) -> None:
        self.session_name = session_name
        self.rows = rows
        self.cols = cols
        self.child: Any = None

    def start(self) -> None:
        import pexpect

        self.child = pexpect.spawn(
            "tmux",
            ["attach-session", "-t", self.session_name],
            dimensions=(self.rows, self.cols),
            encoding="utf-8",
            codec_errors="replace",
        )

    async def read_loop(self, websocket: Any) -> None:
        while self.child is not None and self.child.isalive():
            try:
                data = await asyncio.to_thread(self.child.read_nonblocking, 4096, 0.1)
            except Exception:
                await asyncio.sleep(0.01)
                continue
            if data:
                await websocket.send_json({"type": "output", "data": data})

    async def write_input(self, data: str) -> None:
        if self.child is not None and self.child.isalive():
            await asyncio.to_thread(self.child.send, data)

    async def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        if self.child is not None and self.child.isalive():
            await asyncio.to_thread(self.child.setwinsize, rows, cols)

    def close(self) -> None:
        if self.child is not None:
            self.child.close(force=True)
            self.child = None


def remote_host_port(config: dict[str, Any]) -> tuple[str, int]:
    return remote_local_host_port(config)


def remote_url(config: dict[str, Any], include_token: bool = False) -> str:
    return remote_local_url(config, include_token=include_token)


def _check_runtime() -> str | None:
    if sys.platform == "win32":
        return "Remote mode is only supported on macOS/Linux. Use WSL on Windows."
    if shutil.which("tmux") is None:
        return "Could not find 'tmux' on PATH. Install tmux to use remote mode."
    if shutil.which("claude") is None:
        return "Could not find 'claude' on PATH."
    return None


def _session_exists(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _log_line(message: str) -> str:
    return f"[{_utc_now()}] {message}"


def _log_event(message: str) -> None:
    print(_log_line(message), flush=True)


def _append_log_event(message: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with REMOTE_SERVER_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(_log_line(message) + "\n")


@contextlib.contextmanager
def with_session_registry_lock() -> Iterator[None]:
    with file_lock(REMOTE_SESSIONS_LOCK_PATH):
        yield


@contextlib.contextmanager
def with_remote_hub_lock() -> Iterator[None]:
    with file_lock(REMOTE_HUB_LOCK_PATH):
        yield


def load_session_registry() -> dict[str, Any]:
    try:
        data = json.loads(REMOTE_SESSIONS_PATH.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"version": 1, "sessions": {}}
    if not isinstance(data, dict):
        return {"version": 1, "sessions": {}}
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        data["sessions"] = {}
    data["version"] = 1
    return data


def save_session_registry(registry: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    registry["version"] = 1
    registry.setdefault("sessions", {})
    REMOTE_SESSIONS_PATH.write_text(json.dumps(registry, indent=2))


def register_remote_session(session: dict[str, Any]) -> None:
    with with_session_registry_lock():
        registry = load_session_registry()
        sessions = registry.setdefault("sessions", {})
        sessions[session["id"]] = session
        save_session_registry(registry)


def _session_is_running(session: dict[str, Any]) -> bool:
    tmux_session = session.get("tmux_session")
    return isinstance(tmux_session, str) and _session_exists(tmux_session)


def _running_tmux_sessions() -> set[str]:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#S"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def scan_remote_sessions(prune: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with with_session_registry_lock():
        registry = load_session_registry()
        raw_sessions = registry.setdefault("sessions", {})
        if not isinstance(raw_sessions, dict):
            return [], []
        session_items = list(raw_sessions.items())

    tmux_sessions = _running_tmux_sessions()
    running: list[dict[str, Any]] = []
    dead: list[dict[str, Any]] = []
    dead_ids: list[str] = []
    for session_id, session in session_items:
        if not isinstance(session, dict):
            dead_ids.append(session_id)
            continue
        tmux_session = session.get("tmux_session")
        if isinstance(tmux_session, str) and tmux_session in tmux_sessions:
            running.append(session)
        else:
            dead.append(session)
            dead_ids.append(session_id)
    if prune and dead_ids:
        with with_session_registry_lock():
            registry = load_session_registry()
            sessions = registry.setdefault("sessions", {})
            if isinstance(sessions, dict):
                for session_id in dead_ids:
                    sessions.pop(session_id, None)
                save_session_registry(registry)
    running.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    dead.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return running, dead


def prune_dead_sessions() -> None:
    scan_remote_sessions(prune=True)


def list_remote_sessions() -> list[dict[str, Any]]:
    running, _dead = scan_remote_sessions(prune=True)
    return running


def get_remote_session(session_id: str) -> dict[str, Any] | None:
    running, _dead = scan_remote_sessions(prune=True)
    for session in running:
        if session.get("id") == session_id:
            return session
    return None


def ensure_tmux_session(config: dict[str, Any], args: list[str], session_name: str) -> str | None:
    error = _check_runtime()
    if error is not None:
        return error
    if _session_exists(session_name):
        return f"tmux session '{session_name}' already exists. Choose another session prefix or end the old session."

    env = build_env(config, masked=False)
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, *build_claude_command(args)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown tmux error").strip()
        return f"Failed to create tmux session: {message}"
    return None


def _hub_health_url(config: dict[str, Any]) -> str:
    host, port = remote_host_port(config)
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}/health"


def fetch_hub_health(config: dict[str, Any], timeout: float = 0.5) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        with urlopen(_hub_health_url(config), timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, None, str(exc)
    ok = bool(data.get("ok")) and data.get("name") == REMOTE_HUB_NAME
    return ok, data, None


def _hub_is_running(config: dict[str, Any]) -> bool:
    ok, _data, _error = fetch_hub_health(config)
    return ok


def rotate_remote_server_log() -> None:
    rotate_log(REMOTE_SERVER_LOG_PATH, REMOTE_SERVER_LOG_MAX_BYTES, REMOTE_SERVER_LOG_BACKUPS)


def ensure_remote_hub(config: dict[str, Any]) -> str | None:
    with with_remote_hub_lock():
        if _hub_is_running(config):
            _append_log_event("remote hub already running")
            return None

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        rotate_remote_server_log()
        host, port = remote_host_port(config)
        log_file = REMOTE_SERVER_LOG_PATH.open("ab")
        log_file.write((_log_line(f"starting remote hub process host={host} port={port}") + "\n").encode())
        log_file.flush()
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "ccode.remote", "serve"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            REMOTE_HUB_PID_PATH.write_text(str(process.pid))
        except OSError as exc:
            log_file.write((_log_line(f"failed to start remote hub process error={exc}") + "\n").encode())
            log_file.close()
            return f"Failed to start remote hub: {exc}"

        for _ in range(50):
            if _hub_is_running(config):
                log_file.write((_log_line(f"remote hub health check succeeded pid={process.pid}") + "\n").encode())
                log_file.close()
                return None
            time.sleep(0.1)
        log_file.write((_log_line(f"remote hub health check timed out pid={process.pid}") + "\n").encode())
        log_file.close()
        return f"Failed to start remote hub. See {REMOTE_SERVER_LOG_PATH} for details."


_AUTH_CACHE: tuple[float | None, str, str] = (None, "", "")


def _current_remote_credentials() -> tuple[str, str]:
    global _AUTH_CACHE
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = None
    cached_mtime, app_id, app_key = _AUTH_CACHE
    if cached_mtime == mtime:
        return app_id, app_key
    config = load_config()
    app_id = remote_local_app_id(config).strip()
    app_key = remote_local_app_key(config).strip()
    _AUTH_CACHE = (mtime, app_id, app_key)
    return app_id, app_key


def _valid_auth_payload(payload: dict[str, Any], scope: str) -> AuthResult:
    app_id, app_key = _current_remote_credentials()
    return verify_auth_payload(app_id, app_key, payload, scope)


def _auth_response(result: AuthResult, response_class: Any) -> Any:
    status_code = 401 if result.error in {"auth_required", "reauth_required"} else 403
    return response_class({"error": result.error or "auth_failed", "message": result.message}, status_code=status_code)


async def _attach_websocket_to_session(websocket: Any, session: dict[str, Any]) -> None:
    session_id = str(session.get("id") or "<unknown>")
    tmux_session = session.get("tmux_session")
    if not isinstance(tmux_session, str) or not _session_exists(tmux_session):
        _log_event(f"websocket session not running session_id={session_id}")
        await websocket.send_json({"type": "status", "message": "session is not running"})
        await websocket.close(code=1008)
        return

    _log_event(f"websocket attached session_id={session_id} tmux={tmux_session}")
    await websocket.send_json({"type": "status", "message": "connected"})
    bridge = TerminalBridge(tmux_session)
    reader: asyncio.Task[None] | None = None
    try:
        bridge.start()
        reader = asyncio.create_task(bridge.read_loop(websocket))
        while True:
            raw = await websocket.receive_text()
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
    except Exception:
        _log_event(f"websocket disconnected session_id={session_id}")
    finally:
        if reader is not None:
            reader.cancel()
        bridge.close()
        _log_event(f"websocket bridge closed session_id={session_id}")


def create_app() -> Any:
    try:
        from fastapi import FastAPI, Header, WebSocket as FastAPIWebSocket
        from fastapi.responses import HTMLResponse, JSONResponse, Response
    except ImportError:
        raise RuntimeError(missing_extra_message("remote-server", "fastapi, uvicorn")) from None

    app = FastAPI()

    @app.get("/")
    async def index() -> Response:
        _log_event("served remote index")
        return HTMLResponse(asset_text("index.html"), headers={"Cache-Control": "no-store"})

    @app.get("/app.js")
    async def app_js() -> Response:
        return Response(asset_text("app.js"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/style.css")
    async def style_css() -> Response:
        return Response(asset_text("style.css"), media_type="text/css", headers={"Cache-Control": "no-store"})

    @app.get("/assets/{asset_name}")
    async def web_asset(asset_name: str) -> Response:
        media_types = {
            "xterm.js": "application/javascript",
            "addon-fit.js": "application/javascript",
            "xterm.css": "text/css",
        }
        media_type = media_types.get(asset_name)
        if media_type is None:
            return Response(status_code=404)
        return Response(web_asset_text(asset_name), media_type=media_type, headers={"Cache-Control": "no-store"})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "name": REMOTE_HUB_NAME, "multi_session": True, "remote_kind": "local"}

    @app.get("/api/sessions")
    async def sessions(
        x_ccode_app_id: str | None = Header(default=None),
        x_ccode_timestamp: str | None = Header(default=None),
        x_ccode_sign: str | None = Header(default=None),
    ) -> Response:
        auth_result = _valid_auth_payload({"appId": x_ccode_app_id, "timestamp": x_ccode_timestamp, "sign": x_ccode_sign}, "local.api")
        if not auth_result.ok:
            _log_event(f"api sessions unauthorized error={auth_result.error}")
            return _auth_response(auth_result, JSONResponse)
        sessions = list_remote_sessions()
        _log_event(f"api sessions returned count={len(sessions)}")
        return JSONResponse({"sessions": sessions})

    @app.get("/api/sessions/{session_id}")
    async def session_detail(
        session_id: str,
        x_ccode_app_id: str | None = Header(default=None),
        x_ccode_timestamp: str | None = Header(default=None),
        x_ccode_sign: str | None = Header(default=None),
    ) -> Response:
        auth_result = _valid_auth_payload({"appId": x_ccode_app_id, "timestamp": x_ccode_timestamp, "sign": x_ccode_sign}, "local.api")
        if not auth_result.ok:
            _log_event(f"api session detail unauthorized session_id={session_id} error={auth_result.error}")
            return _auth_response(auth_result, JSONResponse)
        session = get_remote_session(session_id)
        if session is None or not _session_is_running(session):
            _log_event(f"api session detail not_found session_id={session_id}")
            return JSONResponse({"error": "not_found"}, status_code=404)
        _log_event(f"api session detail returned session_id={session_id}")
        return JSONResponse({"session": session})

    @app.get("/api/session")
    async def legacy_session(
        x_ccode_app_id: str | None = Header(default=None),
        x_ccode_timestamp: str | None = Header(default=None),
        x_ccode_sign: str | None = Header(default=None),
    ) -> Response:
        auth_result = _valid_auth_payload({"appId": x_ccode_app_id, "timestamp": x_ccode_timestamp, "sign": x_ccode_sign}, "local.api")
        if not auth_result.ok:
            _log_event(f"legacy api session unauthorized error={auth_result.error}")
            return _auth_response(auth_result, JSONResponse)
        sessions = list_remote_sessions()
        _log_event(f"legacy api session requested running_count={len(sessions)}")
        if len(sessions) == 1:
            session = sessions[0]
            return JSONResponse({"session_name": session["tmux_session"], "running": True, "session": session})
        return JSONResponse({"deprecated": True, "sessions": len(sessions)})

    @app.websocket("/ws/{session_id}")
    async def websocket_session(websocket: FastAPIWebSocket, session_id: str) -> None:
        await websocket.accept()
        _log_event(f"websocket accepted session_id={session_id}")
        auth_result = _valid_auth_payload(dict(websocket.query_params), "local.ws")
        if not auth_result.ok:
            _log_event(f"websocket unauthorized session_id={session_id} error={auth_result.error}")
            await websocket.send_json({"type": "error", "error": auth_result.error, "message": auth_result.message})
            await websocket.close(code=1008)
            return
        session = get_remote_session(session_id)
        if session is None:
            _log_event(f"websocket session not found session_id={session_id}")
            await websocket.send_json({"type": "status", "message": "session not found"})
            await websocket.close(code=1008)
            return
        await _attach_websocket_to_session(websocket, session)

    @app.websocket("/ws")
    async def legacy_websocket(websocket: FastAPIWebSocket) -> None:
        await websocket.accept()
        _log_event("legacy websocket accepted")
        auth_result = _valid_auth_payload(dict(websocket.query_params), "local.ws")
        if not auth_result.ok:
            _log_event(f"legacy websocket unauthorized error={auth_result.error}")
            await websocket.send_json({"type": "error", "error": auth_result.error, "message": auth_result.message})
            await websocket.close(code=1008)
            return
        sessions = list_remote_sessions()
        if len(sessions) != 1:
            _log_event(f"legacy websocket rejected running_count={len(sessions)}")
            await websocket.send_json({"type": "status", "message": "use session list to choose a session"})
            await websocket.close(code=1008)
            return
        await _attach_websocket_to_session(websocket, sessions[0])

    return app


def _attach_tmux_session(session_name: str) -> str | None:
    try:
        result = subprocess.run(["tmux", "attach-session", "-t", session_name], check=False)
    except FileNotFoundError:
        return "Could not find 'tmux' on PATH."
    if result.returncode != 0:
        return f"tmux attach exited with code {result.returncode}."
    return None


def run_remote_hub_forever(config: dict[str, Any]) -> str | None:
    try:
        import uvicorn
    except ImportError:
        return missing_extra_message("remote-server", "uvicorn")

    host, port = remote_host_port(config)
    _log_event(f"remote hub serving host={host} port={port} pid={os.getpid()}")
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    _log_event("remote hub stopped")
    return None


def run_remote_session(config: dict[str, Any], args: list[str]) -> str | None:
    mode = remote_mode(config)
    if mode == "local" and (not remote_local_app_id(config).strip() or not remote_local_app_key(config).strip()):
        return "Remote app ID and app key are required. Set them in the config screen or press g on the APP ID / APP KEY fields to generate them."
    server = remote_server_config(config)
    if mode == "server" and (not remote_server_url(config) or not str(server.get("appId") or "").strip() or not str(server.get("appKey") or "").strip()):
        return "Remote server URL, client app ID, and client app key are required in server mode."
    error = _check_runtime()
    if error is not None:
        return error

    session_name = _new_session_id(config)
    error = ensure_tmux_session(config, args, session_name)
    if error is not None:
        return error

    remote = config.get("remote", {})
    title = _sanitize_session_name(str(remote.get("session_name") or "ccode-claude"))
    now = _utc_now()
    register_remote_session(
        {
            "id": session_name,
            "tmux_session": session_name,
            "title": title,
            "cwd": os.getcwd(),
            "args": args,
            "created_at": now,
            "updated_at": now,
            "owner_pid": os.getpid(),
        }
    )
    _append_log_event(f"registered remote session id={session_name} cwd={os.getcwd()} args={len(args)}")

    if mode == "server":
        server_url = remote_server_url(config)
        if server.get("auto_connect", True):
            try:
                from .remote_client import ensure_remote_client_connector
            except ImportError:
                return missing_extra_message("remote-client", "websockets, pexpect")
            error = ensure_remote_client_connector(config)
            if error is not None:
                return error
        print("ccode remote session started\n")
        print("Server:")
        print(f"  {server_url}")
        print(f"  device: {server.get('device_name') or '<unnamed>'} ({server.get('device_id') or '<unset>'})\n")
        print("Tmux session:")
        print(f"  {session_name}\n")
        if server_url.startswith("http://"):
            print("Security warning:")
            print("  Server URL uses plain HTTP/WS. Use HTTPS/WSS in production.\n")
        if server.get("auto_connect", True):
            print("This terminal will attach to this tmux session. The connector stays running independently.")
            print("Detach with Ctrl+B then D. Browser users can choose this device/session from the server.\n")
        else:
            print("This terminal will attach to this tmux session. Start the connector with `ccode-remote client start`.")
            print("Detach with Ctrl+B then D.\n")
    else:
        error = ensure_remote_hub(config)
        if error is not None:
            return error

        host, _port = remote_host_port(config)
        url = remote_url(config, include_token=False)
        base_url = remote_url(config, include_token=False).rstrip("/")

        print("ccode remote session started\n")
        print("Local:")
        print(f"  {url}\n")
        print("Cloudflare Tunnel:")
        print(f"  cloudflared tunnel --url {base_url}\n")
        print("Tmux session:")
        print(f"  {session_name}\n")
        if host == "0.0.0.0":
            print("Security warning:")
            print("  Listening on 0.0.0.0 exposes all remote sessions to your network. Protect the access URL and app credentials.\n")
        print("This terminal will attach to this tmux session. The web hub stays running independently.")
        print("Detach with Ctrl+B then D. Browser users can choose this session from the session list.\n")

    try:
        return _attach_tmux_session(session_name)
    except KeyboardInterrupt:
        return None


def run_remote_server(config: dict[str, Any], args: list[str]) -> str | None:
    return run_remote_session(config, args)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["serve"]:
        error = run_remote_hub_forever(load_config())
        if error:
            print(error, file=sys.stderr)
            raise SystemExit(1)
        return
    print("Usage: python -m ccode.remote serve", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
