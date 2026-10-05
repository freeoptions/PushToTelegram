from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

from bili_client import BiliClient, BiliClientError, BiliRiskControlError, VideoItem
from pending_retry_store import PendingTelegramRetryStore, PendingVideoItem
from settings import AppConfig
from store import SentVideo, SentVideoStore
from telegram_client import TelegramClient, TelegramError, build_batch_video_message
from youtube_client import YouTubeClient, YouTubeClientError


LogFunc = Callable[[str], None]
MIN_REQUEST_DELAY_SECONDS = 5.0


@dataclass(slots=True)
class CheckResult:
    checked_count: int = 0
    sent_count: int = 0
    failed_count: int = 0


class MonitorService:
    def __init__(
        self,
        config: AppConfig,
        log: LogFunc | None = None,
        error_log: LogFunc | None = None,
    ) -> None:
        self._config = config
        self._log = log or (lambda _: None)
        self._error_log = error_log or self._log

    def send_test_message(self) -> bool:
        telegram = self._build_telegram_client()
        self._log("操作：测试 Telegram")
        try:
            telegram.send_test_message()
        except Exception as exc:
            self._error_log(f"结果：Telegram 测试失败，已跳过当前步骤。{exc}")
            return False
        self._log("结果：Telegram 测试消息发送成功。")
        return True

    def check_updates(self) -> CheckResult:
        self._validate_for_check()
        return self._run_monitor(mode="check")

    def send_recent_videos(self) -> CheckResult:
        self._validate_for_check()
        return self._run_monitor(mode="recent")

    def retry_pending_send(self) -> CheckResult:
        self._validate_for_retry()
        pending_store = PendingTelegramRetryStore()
        pending_items = pending_store.load_items()
        result = CheckResult()
        if not pending_items:
            self._log("结果：当前没有需要重试发送的链接。")
            return result

        telegram = self._build_telegram_client()
        store = SentVideoStore()
        try:
            sent_successfully = self._send_videos(telegram, store, pending_items, result)
        finally:
            store.close()

        if sent_successfully:
            pending_store.clear()
            self._log("结果：重试发送成功，已清空待重试批次。")
        return result

    def _run_monitor(self, mode: str) -> CheckResult:
        store = SentVideoStore()
        result = CheckResult()
        pending_items: list[tuple[str, str, VideoItem]] = []
        checkpoint_updates: list[tuple[str, str, int]] = []
        check_started_at = int(time.time())

        try:
            bili = self._build_bili_client_safe(result)
            youtube = self._build_youtube_client_safe(result)
            telegram = self._build_telegram_client()
            enabled_up_targets = [target for target in self._config.up_targets if target.enabled]
            total_targets = len(enabled_up_targets) + len(self._config.youtube_targets)
            current_index = 0

            if bili is not None:
                for target in enabled_up_targets:
                    action_text = "检查" if mode == "check" else "补发"
                    log_action = (
                        f"操作：检查 UID {target.uid}"
                        if mode == "check"
                        else f"操作：补发 UID {target.uid} 最近投稿"
                    )
                    self._log(log_action)
                    try:
                        incremental_check = False
                        scan_complete = True
                        if mode == "check":
                            checkpoint = store.get_checkpoint("bilibili", target.uid)
                            if checkpoint is not None:
                                incremental_check = True
                                videos, scan_complete = bili.fetch_videos_since(
                                    target.uid,
                                    checkpoint,
                                    page_delay=self._random_request_delay(),
                                )
                            else:
                                sent_bvids = store.get_sent_bvids(target.uid)
                                if sent_bvids:
                                    incremental_check = True
                                    videos, scan_complete = bili.fetch_videos_since(
                                        target.uid,
                                        None,
                                        stop_bvids=sent_bvids,
                                        page_delay=self._random_request_delay(),
                                    )
                                else:
                                    videos = bili.fetch_recent_videos(target.uid, self._config.fetch_count)
                        else:
                            videos = bili.fetch_recent_videos(target.uid, self._config.fetch_count)
                    except BiliRiskControlError as exc:
                        result.failed_count += 1
                        self._error_log(f"结果：{action_text} UID {target.uid} 触发 B站 风控，已中断本次任务。{exc}")
                        raise
                    except BiliClientError as exc:
                        result.failed_count += 1
                        self._error_log(f"结果：{action_text} UID {target.uid} 失败，已跳过该目标。{exc}")
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue
                    except Exception as exc:
                        result.failed_count += 1
                        self._error_log(f"结果：{action_text} UID {target.uid} 发生未预期错误，已跳过该目标。{exc}")
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue

                    result.checked_count += 1
                    if mode == "check" and not scan_complete:
                        result.failed_count += 1
                        self._error_log(
                            f"结果：UID {target.uid} 本次最多翻页 10 页，仍未到达检查边界；"
                            "已处理当前页面，但不会推进检查时间，避免静默漏掉更旧投稿。"
                        )
                    if mode == "check" and scan_complete:
                        checkpoint_updates.append(("bilibili", target.uid, check_started_at))
                    if not videos:
                        self._log(f"结果：UID {target.uid} 没有拿到投稿数据。")
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue

                    if mode == "check":
                        pending = (
                            self._pick_unsent_videos(store, videos)
                            if incremental_check
                            else self._pick_pending_videos(store, target.uid, videos)
                        )
                        empty_message = f"结果：{self._display_name(target.uid, videos[0].up_name, target.label)} 没有新投稿。"
                        collected_message = "结果：收集到待发送"
                    else:
                        pending = self._pick_recent_unsent_videos(store, videos)
                        empty_message = f"结果：{self._display_name(target.uid, videos[0].up_name, target.label)} 最近投稿都已发送过。"
                        collected_message = "结果：收集到待补发"

                    if not pending:
                        self._log(empty_message)
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue

                    for video in pending:
                        pending_items.append(("bilibili", target.label, video))
                        self._log(f"{collected_message}：{video.up_name} / {video.title}")
                    current_index += 1
                    self._sleep_before_next_up(current_index, total_targets)
            else:
                current_index += len(enabled_up_targets)

            if youtube is not None:
                for target in self._config.youtube_targets:
                    action_text = "检查" if mode == "check" else "补发"
                    log_action = (
                        f"操作：检查 YouTube {target.channel_ref}"
                        if mode == "check"
                        else f"操作：补发 YouTube {target.channel_ref} 最近视频"
                    )
                    self._log(log_action)
                    try:
                        channel_id, videos = youtube.fetch_recent_videos(target.channel_ref, self._config.fetch_count)
                    except YouTubeClientError as exc:
                        result.failed_count += 1
                        self._error_log(f"结果：{action_text} YouTube {target.channel_ref} 失败，已跳过该目标。{exc}")
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue
                    except Exception as exc:
                        result.failed_count += 1
                        self._error_log(f"结果：{action_text} YouTube {target.channel_ref} 发生未预期错误，已跳过该目标。{exc}")
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue

                    result.checked_count += 1
                    if not videos:
                        self._log(f"结果：YouTube {target.channel_ref} 没有拿到视频数据。")
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue

                    display_name = self._display_name(channel_id, videos[0].up_name, target.label)
                    if mode == "check":
                        pending = self._pick_pending_videos(store, channel_id, videos)
                        empty_message = f"结果：{display_name} 没有新视频。"
                        collected_message = "结果：收集到待发送"
                    else:
                        pending = self._pick_recent_unsent_videos(store, videos)
                        empty_message = f"结果：{display_name} 最近视频都已发送过。"
                        collected_message = "结果：收集到待补发"

                    if not pending:
                        self._log(empty_message)
                        current_index += 1
                        self._sleep_before_next_up(current_index, total_targets)
                        continue

                    for video in pending:
                        pending_items.append(("youtube", target.label, video))
                        self._log(f"{collected_message}：{video.up_name} / {video.title}")
                    current_index += 1
                    self._sleep_before_next_up(current_index, total_targets)
            else:
                current_index += len(self._config.youtube_targets)

            send_successfully = self._send_videos(telegram, store, pending_items, result)
            if send_successfully and mode == "check":
                self._save_checkpoints(store, checkpoint_updates, result)
            return result
        finally:
            store.close()

    def _send_videos(
        self,
        telegram: TelegramClient,
        store: SentVideoStore,
        items: list[PendingVideoItem],
        result: CheckResult,
    ) -> bool:
        if not items:
            self._log("结果：本次没有需要发送的投稿。")
            return True

        message = build_batch_video_message(items=[video.video_url for _, _, video in items])
        try:
            telegram.send_message(message)
        except (TelegramError, Exception) as exc:
            result.failed_count += len(items)
            self._save_pending_retry_items(items)
            self._error_log(f"结果：发送失败，已跳过当前批次。已保留本批链接，可稍后点击重试发送。{exc}")
            return False

        all_saved = True
        for platform, label, video in items:
            try:
                store.save_sent(
                    SentVideo(
                        platform=platform,
                        uid=video.uid,
                        up_name=video.up_name,
                        bvid=video.bvid,
                        title=video.title,
                        video_url=video.video_url,
                        published_at=video.published_at,
                    )
                )
            except Exception as exc:
                all_saved = False
                result.failed_count += 1
                self._error_log(f"结果：发送历史写入失败，已跳过该记录。{video.up_name} / {video.title}。{exc}")
                continue

            result.sent_count += 1
            self._log(f"结果：已成功发送到 Telegram：{self._display_name(video.uid, video.up_name, label)} / {video.title}")
        return all_saved

    def _save_checkpoints(
        self,
        store: SentVideoStore,
        updates: list[tuple[str, str, int]],
        result: CheckResult,
    ) -> None:
        for platform, uid, check_started_at in updates:
            try:
                store.save_checkpoint(platform, uid, check_started_at)
            except Exception as exc:
                result.failed_count += 1
                self._error_log(f"结果：保存 {platform} UID {uid} 的检查时间失败，下次将重新检查。{exc}")

    def _save_pending_retry_items(self, items: list[PendingVideoItem]) -> None:
        try:
            PendingTelegramRetryStore().replace_items(items)
        except Exception as exc:
            self._error_log(f"结果：保存待重试链接失败，请先不要关闭软件。{exc}")

    def _build_bili_client_safe(self, result: CheckResult) -> BiliClient | None:
        try:
            return self._build_bili_client()
        except Exception as exc:
            enabled_up_count = sum(1 for target in self._config.up_targets if target.enabled)
            if enabled_up_count:
                result.failed_count += enabled_up_count
                self._error_log(f"结果：B站 客户端初始化失败，已跳过全部 B站 目标。{exc}")
            return None

    def _build_youtube_client_safe(self, result: CheckResult) -> YouTubeClient | None:
        try:
            return self._build_youtube_client()
        except Exception as exc:
            if self._config.youtube_targets:
                result.failed_count += len(self._config.youtube_targets)
                self._error_log(f"结果：YouTube 客户端初始化失败，已跳过全部 YouTube 目标。{exc}")
            return None

    def _pick_pending_videos(
        self,
        store: SentVideoStore,
        uid: str,
        videos: list[VideoItem],
    ) -> list[VideoItem]:
        if not store.has_any_for_uid(uid):
            pending = videos[: max(1, self._config.first_sync_count)]
            pending.reverse()
            return pending

        pending: list[VideoItem] = []
        for video in videos:
            if store.was_sent(uid, video.bvid):
                break
            pending.append(video)
        pending.reverse()
        return pending

    def _pick_recent_unsent_videos(
        self,
        store: SentVideoStore,
        videos: list[VideoItem],
    ) -> list[VideoItem]:
        limit = max(1, self._config.first_sync_count)
        pending: list[VideoItem] = []
        for video in videos:
            if store.was_sent(video.uid, video.bvid):
                continue
            pending.append(video)
            if len(pending) >= limit:
                break
        pending.reverse()
        return pending

    @staticmethod
    def _pick_unsent_videos(
        store: SentVideoStore,
        videos: list[VideoItem],
    ) -> list[VideoItem]:
        pending = [video for video in videos if not store.was_sent(video.uid, video.bvid)]
        pending.reverse()
        return pending

    def _sleep_before_next_up(self, current_index: int, total: int) -> None:
        if current_index >= total - 1:
            return
        self._sleep_request_interval()

    def _sleep_request_interval(self) -> None:
        sleep_seconds = self._random_request_delay()
        if sleep_seconds <= 0:
            return

        self._log(f"结果：等待 {sleep_seconds} 秒后检查下一个 UP。")
        time.sleep(sleep_seconds)

    def _random_request_delay(self) -> float:
        min_seconds = max(MIN_REQUEST_DELAY_SECONDS, self._config.request_interval_seconds_min)
        max_seconds = max(min_seconds, self._config.request_interval_seconds_max)
        if max_seconds <= 0:
            return 0.0
        return round(random.uniform(min_seconds, max_seconds), 1)

    def _build_bili_client(self) -> BiliClient:
        return BiliClient(
            cookie=self._config.bili_cookie,
            use_browser_cookie=self._config.use_browser_cookie,
        )

    def _build_telegram_client(self) -> TelegramClient:
        return TelegramClient(
            bot_token=self._config.bot_token,
            chat_id=self._config.chat_id,
        )

    def _build_youtube_client(self) -> YouTubeClient:
        return YouTubeClient()

    def _validate_for_check(self) -> None:
        if not self._config.bot_token:
            raise ValueError("请先填写 Telegram Bot Token。")
        if not self._config.chat_id:
            raise ValueError("请先填写 Telegram Chat ID。")
        if not any(target.enabled for target in self._config.up_targets) and not self._config.youtube_targets:
            raise ValueError("请至少启用一个 B站 UP 或填写一个 YouTube 频道。")

    def _validate_for_retry(self) -> None:
        if not self._config.bot_token:
            raise ValueError("请先填写 Telegram Bot Token。")
        if not self._config.chat_id:
            raise ValueError("请先填写 Telegram Chat ID。")

    @staticmethod
    def _display_name(uid: str, up_name: str, label: str) -> str:
        label = label.strip()
        if label:
            return f"{label}({uid})"
        return up_name or uid
