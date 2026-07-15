from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from bili_client import BrowserCookieReadError, export_browser_cookie_header
from monitor_service import MonitorService
from settings import AppConfig, UpTarget, YouTubeTarget, ensure_data_dir
from store import SentVideoStore


def _parse_targets(raw_items: list[dict[str, Any]] | None) -> list[UpTarget]:
    targets: list[UpTarget] = []
    seen: set[str] = set()
    for item in raw_items or []:
        uid = str((item or {}).get("uid", "")).strip()
        label = str((item or {}).get("label", "")).strip()
        enabled = bool((item or {}).get("enabled", True))
        if not uid or not uid.isdigit() or uid in seen:
            continue
        seen.add(uid)
        targets.append(UpTarget(uid=uid, label=label, enabled=enabled))
    return targets


def _parse_youtube_targets(raw_items: list[dict[str, Any]] | None) -> list[YouTubeTarget]:
    targets: list[YouTubeTarget] = []
    seen: set[str] = set()
    for item in raw_items or []:
        channel_ref = str((item or {}).get("channel_ref", "")).strip()
        label = str((item or {}).get("label", "")).strip()
        key = channel_ref.casefold()
        if not channel_ref or key in seen:
            continue
        seen.add(key)
        targets.append(YouTubeTarget(channel_ref=channel_ref, label=label))
    return targets


def _config_from_payload(payload: dict[str, Any]) -> AppConfig:
    request_min = max(0.0, float(payload.get("request_interval_seconds_min", payload.get("request_interval_seconds", 8.0)) or 0.0))
    request_max = max(request_min, float(payload.get("request_interval_seconds_max", payload.get("request_interval_seconds", request_min)) or request_min))

    return AppConfig(
        bot_token=str(payload.get("bot_token", "")).strip(),
        chat_id=str(payload.get("chat_id", "")).strip(),
        up_targets=_parse_targets(payload.get("up_targets")),
        youtube_targets=_parse_youtube_targets(payload.get("youtube_targets")),
        bili_cookie=str(payload.get("bili_cookie", "")).strip(),
        use_browser_cookie=bool(payload.get("use_browser_cookie", True)),
        auto_check_hours=max(0, int(payload.get("auto_check_hours", 0) or 0)),
        fetch_count=max(1, int(payload.get("fetch_count", 10) or 10)),
        first_sync_count=max(1, int(payload.get("first_sync_count", 10) or 10)),
        request_interval_seconds_min=request_min,
        request_interval_seconds_max=request_max,
        message_interval_seconds=max(0.0, float(payload.get("message_interval_seconds", 1.5) or 0.0)),
        export_dir=str(payload.get("export_dir", "")).strip(),
    )


class BackendState:
    def __init__(self) -> None:
        ensure_data_dir()
        self._lock = threading.Lock()

    def load_config(self) -> AppConfig:
        with self._lock:
            return AppConfig.load()

    def save_config(self, payload: dict[str, Any]) -> AppConfig:
        with self._lock:
            config = _config_from_payload(payload)
            config.save()
            return config

    def export_state(self) -> dict[str, Any]:
        with self._lock:
            config = AppConfig.load()
            store = SentVideoStore()
            try:
                sent_videos = [asdict(item) for item in store.list_sent_videos()]
            finally:
                store.close()
            return {
                "config": asdict(config),
                "sent_videos": sent_videos,
            }

    def run_service_action(self, payload: dict[str, Any], action: str) -> dict[str, Any]:
        with self._lock:
            config = _config_from_payload(payload)
            logs: list[str] = []
            service = MonitorService(config, log=logs.append)
            if action == "test":
                service.send_test_message()
                return {"ok": True, "message": "操作：测试 Telegram", "logs": logs}
            if action == "check":
                result = service.check_updates()
                message = (
                    f"操作：立即检查投稿，结果：成功检查 {result.checked_count} 个目标，"
                    f"失败 {result.failed_count} 个，发送 {result.sent_count} 条链接。"
                )
                return {"ok": True, "message": message, "logs": logs, "result": asdict(result)}
        raise ValueError(f"Unsupported action: {action}")


STATE = BackendState()


class BackendHandler(BaseHTTPRequestHandler):
    server_version = "PushToBiliBackend/1.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/config":
            config = STATE.load_config()
            self._json(HTTPStatus.OK, {"ok": True, "config": asdict(config)})
            return
        if path == "/export/state":
            result = STATE.export_state()
            self._json(HTTPStatus.OK, {"ok": True, "result": result})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/config/save":
                config = STATE.save_config(payload)
                self._json(HTTPStatus.OK, {"ok": True, "config": asdict(config)})
                return
            if path == "/cookie/read":
                cookie = export_browser_cookie_header()
                self._json(HTTPStatus.OK, {"ok": True, "cookie": cookie})
                return
            if path == "/task/test":
                self._json(HTTPStatus.OK, STATE.run_service_action(payload, "test"))
                return
            if path == "/task/check":
                self._json(HTTPStatus.OK, STATE.run_service_action(payload, "check"))
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})
        except BrowserCookieReadError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 18765), BackendHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
