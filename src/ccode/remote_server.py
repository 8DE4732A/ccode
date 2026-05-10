import argparse
import asyncio
import contextlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .remote_common import AuthResult, asset_text, missing_extra_message, verify_auth_payload, web_asset_text

REMOTE_SERVER_NAME = "ccode-remote-server"
ATTACH_TTL_SECONDS = 30


@dataclass
class DeviceConnection:
    device_id: str
    device_name: str
    version: str
    websocket: Any | None = None
    sessions: list[dict[str, Any]] = field(default_factory=list)
    online: bool = True
    connected_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class PendingAttach:
    attach_id: str
    device_id: str
    session_id: str
    created_at: float = field(default_factory=time.time)
    client_ws: Any | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


_DEVICES: dict[str, DeviceConnection] = {}
_PENDING_ATTACHES: dict[str, PendingAttach] = {}
DEVICE_OFFLINE_TTL_SECONDS = 24 * 60 * 60


def _admin_app_id() -> str:
    return os.environ.get("CCODE_REMOTE_SERVER_ADMIN_APP_ID", "").strip()


def _admin_app_key() -> str:
    return os.environ.get("CCODE_REMOTE_SERVER_ADMIN_APP_KEY", "").strip()


def _client_app_id() -> str:
    return os.environ.get("CCODE_REMOTE_SERVER_CLIENT_APP_ID", "").strip()


def _client_app_key() -> str:
    return os.environ.get("CCODE_REMOTE_SERVER_CLIENT_APP_KEY", "").strip()


def _valid_admin(payload: dict[str, Any], scope: str) -> AuthResult:
    return verify_auth_payload(_admin_app_id(), _admin_app_key(), payload, scope)


def _valid_client(payload: dict[str, Any], scope: str) -> AuthResult:
    return verify_auth_payload(_client_app_id(), _client_app_key(), payload, scope)


def _auth_response(result: AuthResult, response_class: Any) -> Any:
    status_code = 401 if result.error in {"auth_required", "reauth_required"} else 403
    return response_class({"error": result.error or "auth_failed", "message": result.message}, status_code=status_code)


def _device_payload(device: DeviceConnection) -> dict[str, Any]:
    return {
        "device_id": device.device_id,
        "device_name": device.device_name,
        "version": device.version,
        "online": device.online,
        "connected_at": device.connected_at,
        "updated_at": device.updated_at,
        "sessions": len(device.sessions),
    }


def _session_payload(device: DeviceConnection, session: dict[str, Any]) -> dict[str, Any]:
    payload = dict(session)
    payload["device_id"] = device.device_id
    payload["device_name"] = device.device_name
    payload["online"] = device.online
    return payload


def _prune_offline_devices() -> None:
    now = time.time()
    expired = [
        device_id
        for device_id, device in _DEVICES.items()
        if not device.online and now - device.updated_at > DEVICE_OFFLINE_TTL_SECONDS
    ]
    for device_id in expired:
        _DEVICES.pop(device_id, None)


def _flatten_sessions() -> list[dict[str, Any]]:
    _prune_offline_devices()
    sessions: list[dict[str, Any]] = []
    for device in _DEVICES.values():
        for session in device.sessions:
            sessions.append(_session_payload(device, session))
    sessions.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return sessions


def _get_device_session(device_id: str, session_id: str) -> tuple[DeviceConnection | None, dict[str, Any] | None]:
    device = _DEVICES.get(device_id)
    if device is None:
        return None, None
    for session in device.sessions:
        if session.get("id") == session_id:
            return device, session
    return device, None


def _prune_pending() -> None:
    now = time.time()
    expired = [attach_id for attach_id, pending in _PENDING_ATTACHES.items() if now - pending.created_at > ATTACH_TTL_SECONDS]
    for attach_id in expired:
        _PENDING_ATTACHES.pop(attach_id, None)


async def _relay_websockets(left: Any, right: Any) -> None:
    async def pump(src: Any, dst: Any) -> None:
        while True:
            message = await src.receive_text()
            await dst.send_text(message)

    left_to_right = asyncio.create_task(pump(left, right))
    right_to_left = asyncio.create_task(pump(right, left))
    done, pending = await asyncio.wait({left_to_right, right_to_left}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        with contextlib.suppress(Exception):
            task.result()


def create_server_app() -> Any:
    try:
        from fastapi import FastAPI, Header, WebSocket as FastAPIWebSocket
        from fastapi.responses import HTMLResponse, JSONResponse, Response
    except ImportError:
        raise RuntimeError(missing_extra_message("remote-server", "fastapi, uvicorn")) from None
    app = FastAPI()

    @app.get("/")
    async def index() -> Response:
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
        _prune_offline_devices()
        return {"ok": True, "name": REMOTE_SERVER_NAME, "devices": len(_DEVICES), "remote_kind": "server"}

    @app.get("/api/devices")
    async def devices(
        x_ccode_app_id: str | None = Header(default=None),
        x_ccode_timestamp: str | None = Header(default=None),
        x_ccode_sign: str | None = Header(default=None),
    ) -> Response:
        auth_result = _valid_admin({"appId": x_ccode_app_id, "timestamp": x_ccode_timestamp, "sign": x_ccode_sign}, "server.api")
        if not auth_result.ok:
            return _auth_response(auth_result, JSONResponse)
        _prune_offline_devices()
        return JSONResponse({"devices": [_device_payload(device) for device in _DEVICES.values()]})

    @app.get("/api/sessions")
    async def sessions(
        x_ccode_app_id: str | None = Header(default=None),
        x_ccode_timestamp: str | None = Header(default=None),
        x_ccode_sign: str | None = Header(default=None),
    ) -> Response:
        auth_result = _valid_admin({"appId": x_ccode_app_id, "timestamp": x_ccode_timestamp, "sign": x_ccode_sign}, "server.api")
        if not auth_result.ok:
            return _auth_response(auth_result, JSONResponse)
        return JSONResponse({"sessions": _flatten_sessions()})

    @app.get("/api/devices/{device_id}/sessions/{session_id}")
    async def session_detail(
        device_id: str,
        session_id: str,
        x_ccode_app_id: str | None = Header(default=None),
        x_ccode_timestamp: str | None = Header(default=None),
        x_ccode_sign: str | None = Header(default=None),
    ) -> Response:
        auth_result = _valid_admin({"appId": x_ccode_app_id, "timestamp": x_ccode_timestamp, "sign": x_ccode_sign}, "server.api")
        if not auth_result.ok:
            return _auth_response(auth_result, JSONResponse)
        device, session = _get_device_session(device_id, session_id)
        if device is None or session is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({"session": _session_payload(device, session)})

    @app.websocket("/client/ws")
    async def client_control(websocket: FastAPIWebSocket) -> None:
        await websocket.accept()
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            hello = json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await websocket.close(code=1008)
            return
        auth_result = _valid_client(hello, "client.control")
        if hello.get("type") != "hello" or not auth_result.ok:
            await websocket.send_json({"type": "error", "error": auth_result.error or "auth_failed", "message": auth_result.message})
            await websocket.close(code=1008)
            return
        device_id = str(hello.get("device_id") or secrets.token_urlsafe(16))
        device = DeviceConnection(
            device_id=device_id,
            device_name=str(hello.get("device_name") or device_id),
            version=str(hello.get("version") or "unknown"),
            websocket=websocket,
            sessions=hello.get("sessions") if isinstance(hello.get("sessions"), list) else [],
        )
        _DEVICES[device_id] = device
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "heartbeat":
                    sessions = payload.get("sessions")
                    if isinstance(sessions, list):
                        device.sessions = [item for item in sessions if isinstance(item, dict)]
                    device.online = True
                    device.updated_at = time.time()
        except Exception:
            if _DEVICES.get(device_id) is device:
                device.online = False
                device.websocket = None
                device.updated_at = time.time()

    @app.websocket("/client/attach/{attach_id}")
    async def client_attach(websocket: FastAPIWebSocket, attach_id: str) -> None:
        await websocket.accept()
        _prune_pending()
        pending = _PENDING_ATTACHES.get(attach_id)
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            payload = json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await websocket.close(code=1008)
            return
        auth_result = _valid_client(payload, "client.attach")
        if pending is None or payload.get("type") != "auth" or not auth_result.ok:
            await websocket.send_json({"type": "error", "error": auth_result.error or "auth_failed", "message": auth_result.message})
            await websocket.close(code=1008)
            return
        pending.client_ws = websocket
        pending.ready.set()
        while attach_id in _PENDING_ATTACHES:
            await asyncio.sleep(1)

    @app.websocket("/ws/devices/{device_id}/sessions/{session_id}")
    async def browser_attach(websocket: FastAPIWebSocket, device_id: str, session_id: str) -> None:
        await websocket.accept()
        auth_result = _valid_admin(dict(websocket.query_params), "server.ws")
        if not auth_result.ok:
            await websocket.send_json({"type": "error", "error": auth_result.error, "message": auth_result.message})
            await websocket.close(code=1008)
            return
        device, session = _get_device_session(device_id, session_id)
        if device is None or session is None or not device.online or device.websocket is None:
            await websocket.send_json({"type": "status", "message": "device/session offline"})
            await websocket.close(code=1008)
            return
        attach_id = secrets.token_urlsafe(32)
        pending = PendingAttach(attach_id=attach_id, device_id=device_id, session_id=session_id)
        _PENDING_ATTACHES[attach_id] = pending
        try:
            await device.websocket.send_json({"type": "attach_request", "attach_id": attach_id, "session_id": session_id})
            await websocket.send_json({"type": "status", "message": "waiting for device"})
            await asyncio.wait_for(pending.ready.wait(), timeout=ATTACH_TTL_SECONDS)
            if pending.client_ws is None:
                await websocket.close(code=1011)
                return
            await _relay_websockets(websocket, pending.client_ws)
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "status", "message": "device attach timed out"})
            await websocket.close(code=1011)
        except Exception:
            with contextlib.suppress(Exception):
                await websocket.close()
        finally:
            _PENDING_ATTACHES.pop(attach_id, None)
            if pending.client_ws is not None:
                with contextlib.suppress(Exception):
                    await pending.client_ws.close()

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccode-remote-server")
    parser.add_argument("--host", default=os.environ.get("CCODE_REMOTE_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CCODE_REMOTE_SERVER_PORT", "8765")))
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print(missing_extra_message("remote-server", "uvicorn"))
        raise SystemExit(1)
    uvicorn.run(create_server_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
