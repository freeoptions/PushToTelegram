from __future__ import annotations

import unittest
from unittest.mock import patch

from bili_client import BiliClient, VideoItem
from store import SentVideoStore


def make_video(video_id: str, published_at: int) -> VideoItem:
    return VideoItem(
        uid="123",
        up_name="测试UP",
        bvid=video_id,
        title=video_id,
        video_url=f"https://www.bilibili.com/video/{video_id}",
        published_at=published_at,
    )


class BiliPaginationTests(unittest.TestCase):
    def test_fetch_videos_since_walks_until_time_boundary(self) -> None:
        client = BiliClient.__new__(BiliClient)
        page_one = [make_video(f"BV{i}", 2100 - i) for i in range(30)]
        page_two = [make_video("BV30", 2000), make_video("BV31", 1980)]

        def fake_fetch_page(uid: str, *, page: int, page_size: int):
            self.assertEqual(uid, "123")
            self.assertEqual(page_size, 30)
            return (page_one, 60) if page == 1 else (page_two, 60)

        with patch.object(client, "_fetch_video_page", side_effect=fake_fetch_page):
            videos, scan_complete = client.fetch_videos_since("123", 1985)

        self.assertTrue(scan_complete)
        self.assertEqual([video.bvid for video in videos], [f"BV{i}" for i in range(30)] + ["BV30"])

    def test_fetch_videos_since_stops_at_known_sent_video(self) -> None:
        client = BiliClient.__new__(BiliClient)
        page_one = [
            make_video("BV-new-2", 2002),
            make_video("BV-new-1", 2001),
            make_video("BV-sent", 2000),
            make_video("BV-old", 1999),
        ]

        with patch.object(client, "_fetch_video_page", return_value=(page_one, 4)):
            videos, scan_complete = client.fetch_videos_since("123", None, stop_bvids={"BV-sent"})

        self.assertTrue(scan_complete)
        self.assertEqual([video.bvid for video in videos], ["BV-new-2", "BV-new-1"])

    def test_fetch_videos_since_stops_after_ten_pages(self) -> None:
        client = BiliClient.__new__(BiliClient)
        calls: list[int] = []

        def fake_fetch_page(uid: str, *, page: int, page_size: int):
            calls.append(page)
            return ([make_video(f"BV{page}-{index}", 3000 - page * 30 - index) for index in range(30)], 10000)

        with patch.object(client, "_fetch_video_page", side_effect=fake_fetch_page):
            videos, scan_complete = client.fetch_videos_since("123", 0)

        self.assertFalse(scan_complete)
        self.assertEqual(calls, list(range(1, 11)))
        self.assertEqual(len(videos), 300)


class SyncCheckpointTests(unittest.TestCase):
    def test_checkpoint_round_trip(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir, patch("store.DB_PATH", Path(temp_dir) / "history.db"):
            store = SentVideoStore()
            try:
                self.assertIsNone(store.get_checkpoint("bilibili", "123"))
                store.save_checkpoint("bilibili", "123", 1710000000)
                self.assertEqual(store.get_checkpoint("bilibili", "123"), 1710000000)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
