from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _resolve_app_dir() -> Path:
    env_base_dir = os.environ.get("PUSH_TO_BILI_APP_DIR", "").strip()
    if env_base_dir:
        return Path(env_base_dir).resolve()
    portable_dir = os.environ.get("PORTABLE_EXECUTABLE_DIR", "").strip()
    if portable_dir:
        return Path(portable_dir).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _resolve_app_dir()
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "history.db"
LOG_PATH = APP_DIR / "app.log"
ICON_PATH = APP_DIR / "push_to_bili_icon.ico"
QT_ICON_PATH = APP_DIR / "push_to_bili_qt.ico"
CHECK_MARK_PATH = APP_DIR / "check_mark_green.png"


def _migrate_legacy_data() -> None:
    if not DATA_DIR.exists():
        return

    mapping = {
        DATA_DIR / "config.json": CONFIG_PATH,
        DATA_DIR / "history.db": DB_PATH,
        DATA_DIR / "app.log": LOG_PATH,
        DATA_DIR / "push_to_bili_icon.ico": ICON_PATH,
        DATA_DIR / "push_to_bili_qt.ico": QT_ICON_PATH,
        DATA_DIR / "check_mark_green.png": CHECK_MARK_PATH,
    }

    for source, target in mapping.items():
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


@dataclass(slots=True)
class UpTarget:
    uid: str
    label: str = ""
    enabled: bool = True


@dataclass(slots=True)
class YouTubeTarget:
    channel_ref: str
    label: str = ""


@dataclass(slots=True)
class AppConfig:
    bot_token: str = ""
    chat_id: str = ""
    up_targets: list[UpTarget] = field(default_factory=list)
    youtube_targets: list[YouTubeTarget] = field(default_factory=list)
    bili_cookie: str = ""
    use_browser_cookie: bool = True
    auto_check_hours: int = 0
    fetch_count: int = 10
    first_sync_count: int = 10
    request_interval_seconds_min: float = 8.0
    request_interval_seconds_max: float = 8.0
    message_interval_seconds: float = 1.5
    export_dir: str = ""

    @classmethod
    def load(cls) -> "AppConfig":
        ensure_data_dir()
        _migrate_legacy_data()
        if not CONFIG_PATH.exists():
            return cls()

        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        targets = [
            UpTarget(
                uid=str(item.get("uid", "")).strip(),
                label=str(item.get("label", "")).strip(),
                enabled=bool(item.get("enabled", True)),
            )
            for item in payload.get("up_targets", [])
            if str(item.get("uid", "")).strip()
        ]
        youtube_targets = [
            YouTubeTarget(
                channel_ref=str(item.get("channel_ref", "")).strip(),
                label=str(item.get("label", "")).strip(),
            )
            for item in payload.get("youtube_targets", [])
            if str(item.get("channel_ref", "")).strip()
        ]

        request_min = float(payload.get("request_interval_seconds_min", payload.get("request_interval_seconds", 8.0)) or 0.0)
        request_max = float(payload.get("request_interval_seconds_max", payload.get("request_interval_seconds", request_min)) or request_min)
        if request_max < request_min:
            request_max = request_min

        return cls(
            bot_token=str(payload.get("bot_token", "")).strip(),
            chat_id=str(payload.get("chat_id", "")).strip(),
            up_targets=targets,
            youtube_targets=youtube_targets,
            bili_cookie=str(payload.get("bili_cookie", "")).strip(),
            use_browser_cookie=bool(payload.get("use_browser_cookie", True)),
            auto_check_hours=max(0, int(payload.get("auto_check_hours", 0) or 0)),
            fetch_count=max(1, int(payload.get("fetch_count", 10) or 10)),
            first_sync_count=max(1, int(payload.get("first_sync_count", 10) or 10)),
            request_interval_seconds_min=max(0.0, request_min),
            request_interval_seconds_max=max(0.0, request_max),
            message_interval_seconds=max(0.0, float(payload.get("message_interval_seconds", 1.5) or 0.0)),
            export_dir=str(payload.get("export_dir", "")).strip(),
        )

    def save(self) -> None:
        ensure_data_dir()
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def ensure_data_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
