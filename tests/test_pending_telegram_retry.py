from __future__ import annotations

import unittest
from unittest.mock import patch

from bili_client import VideoItem
from monitor_service import CheckResult, MonitorService
from settings import AppConfig
from telegram_client import TelegramError


def make_video(video_id: str = "BV1") -> VideoItem:
    return VideoItem(
        uid="1080997637",
        up_name="测试UP",
        bvid=video_id,
        title="测试投稿",
        video_url=f"https://www.bilibili.com/video/{video_id}",
        published_at=1710000000,
    )


class FailingTelegram:
    def send_message(self, text: str) -> None:
        raise TelegramError("ConnectionResetError(10054)")


class RecordingTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str) -> None:
        self.messages.append(text)


class RecordingPendingStore:
    def __init__(self, items=None) -> None:
        self.items = list(items or [])
        self.replace_calls: list[list[tuple[str, str, VideoItem]]] = []
        self.clear_calls = 0

    def replace_items(self, items) -> None:
        self.items = list(items)
        self.replace_calls.append(list(items))

    def load_items(self):
        return list(self.items)

    def clear(self) -> None:
        self.items = []
        self.clear_calls += 1

    def remove_items(self, items) -> None:
        remove_keys = {(video.uid, video.bvid) for _platform, _label, video in items}
        self.items = [
            item
            for item in self.items
            if (item[2].uid, item[2].bvid) not in remove_keys
        ]


class RecordingSentStore:
    def __init__(self) -> None:
        self.saved = []

    def was_sent(self, uid: str, bvid: str) -> bool:
        return False

    def save_sent(self, item) -> None:
        self.saved.append(item)

    def close(self) -> None:
        pass


class PendingTelegramRetryTests(unittest.TestCase):
    def test_telegram_send_failure_saves_current_batch_for_retry(self) -> None:
        video = make_video()
        pending_store = RecordingPendingStore()
        result = CheckResult()
        errors: list[str] = []
        service = MonitorService(AppConfig(bot_token="token", chat_id="chat"), error_log=errors.append)

        with patch("monitor_service.PendingTelegramRetryStore", return_value=pending_store):
            service._send_videos(FailingTelegram(), RecordingSentStore(), [("bilibili", "备注", video)], result)

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(pending_store.replace_calls, [[("bilibili", "备注", video)]])
        self.assertIn("已保留本批链接，可稍后点击重试发送", errors[-1])

    def test_retry_pending_send_sends_saved_batch_and_clears_it(self) -> None:
        video = make_video("BV2")
        pending_store = RecordingPendingStore([("bilibili", "备注", video)])
        sent_store = RecordingSentStore()
        telegram = RecordingTelegram()
        logs: list[str] = []
        service = MonitorService(AppConfig(bot_token="token", chat_id="chat"), log=logs.append, error_log=logs.append)
        service._build_telegram_client = lambda: telegram

        with (
            patch("monitor_service.PendingTelegramRetryStore", return_value=pending_store),
            patch("monitor_service.SentVideoStore", return_value=sent_store),
        ):
            result = service.retry_pending_send()

        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(telegram.messages, [video.video_url])
        self.assertEqual(sent_store.saved[0].bvid, "BV2")
        self.assertEqual(pending_store.clear_calls, 1)
        self.assertEqual(pending_store.items, [])
        self.assertIn("结果：重试发送成功", "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
