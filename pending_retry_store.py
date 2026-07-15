from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from bili_client import VideoItem
from settings import APP_DIR, ensure_data_dir


PENDING_TELEGRAM_RETRY_PATH = APP_DIR / "pending_telegram_retry.json"


PendingVideoItem = tuple[str, str, VideoItem]


class PendingTelegramRetryStore:
    def __init__(self) -> None:
        ensure_data_dir()
        self._path = PENDING_TELEGRAM_RETRY_PATH

    def replace_items(self, items: list[PendingVideoItem]) -> None:
        payload = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": [
                {
                    "platform": platform,
                    "label": label,
                    "video": asdict(video),
                }
                for platform, label, video in items
            ],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_items(self) -> list[PendingVideoItem]:
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        items: list[PendingVideoItem] = []
        for raw_item in payload.get("items", []):
            raw_video = raw_item.get("video") or {}
            try:
                video = VideoItem(
                    uid=str(raw_video.get("uid", "")).strip(),
                    up_name=str(raw_video.get("up_name", "")).strip(),
                    bvid=str(raw_video.get("bvid", "")).strip(),
                    title=str(raw_video.get("title", "")).strip(),
                    video_url=str(raw_video.get("video_url", "")).strip(),
                    published_at=int(raw_video.get("published_at", 0) or 0),
                )
            except (TypeError, ValueError):
                continue
            if not video.uid or not video.bvid or not video.video_url:
                continue
            platform = str(raw_item.get("platform", "bilibili")).strip() or "bilibili"
            label = str(raw_item.get("label", "")).strip()
            items.append((platform, label, video))
        return items

    def clear(self) -> None:
        self.replace_items([])

    def remove_items(self, items: list[PendingVideoItem]) -> None:
        remove_keys = {(video.uid, video.bvid) for _platform, _label, video in items}
        remaining = [
            item
            for item in self.load_items()
            if (item[2].uid, item[2].bvid) not in remove_keys
        ]
        self.replace_items(remaining)
