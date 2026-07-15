from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import requests
from requests import RequestException

from bili_client import VideoItem


YOUTUBE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class YouTubeClientError(RuntimeError):
    pass


class YouTubeClient:
    def __init__(self, timeout: int = 20) -> None:
        self._session = requests.Session()
        self._session.headers.update(YOUTUBE_HEADERS)
        self._timeout = timeout

    def fetch_recent_videos(self, channel_ref: str, count: int = 10) -> tuple[str, list[VideoItem]]:
        try:
            channel_id = self.resolve_channel_ref(channel_ref)
            response = self._session.get(
                "https://www.youtube.com/feeds/videos.xml",
                params={"channel_id": channel_id},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except RequestException as exc:
            raise self._request_error("获取 YouTube RSS", exc) from exc

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise YouTubeClientError(f"YouTube RSS 解析失败：{exc}") from exc

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        entries = root.findall("atom:entry", ns)
        if not entries:
            return channel_id, []

        videos: list[VideoItem] = []
        for entry in entries[: max(1, min(count, 30))]:
            video_id = (entry.findtext("yt:videoId", default="", namespaces=ns) or "").strip()
            if not video_id:
                continue

            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip() or video_id
            published_raw = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
            published_at = self._parse_published_at(published_raw)
            link = entry.find("atom:link", ns)
            video_url = (link.get("href", "").strip() if link is not None else "") or f"https://www.youtube.com/watch?v={video_id}"
            if "/shorts/" in video_url:
                continue

            author = entry.find("atom:author", ns)
            author_name = ""
            if author is not None:
                author_name = (author.findtext("atom:name", default="", namespaces=ns) or "").strip()

            videos.append(
                VideoItem(
                    uid=channel_id,
                    up_name=author_name or channel_id,
                    bvid=video_id,
                    title=title,
                    video_url=video_url,
                    published_at=published_at,
                )
            )

        return channel_id, videos

    def resolve_channel_ref(self, channel_ref: str) -> str:
        raw = channel_ref.strip()
        if not raw:
            raise YouTubeClientError("YouTube 频道标识为空。")

        if self._is_channel_id(raw):
            return raw

        if raw.startswith("@"):
            return self._resolve_channel_id_from_page(self._normalize_channel_url(f"https://www.youtube.com/{raw}"))

        if raw.startswith(("http://", "https://")):
            normalized_url = self._normalize_channel_url(raw)
            parsed = urlparse(normalized_url)
            query = parse_qs(parsed.query)

            feed_channel_id = (query.get("channel_id") or [""])[0].strip()
            if self._is_channel_id(feed_channel_id):
                return feed_channel_id

            match = re.search(r"/channel/(UC[\w-]+)", parsed.path)
            if match:
                return match.group(1)

            return self._resolve_channel_id_from_page(normalized_url)

        raise YouTubeClientError("YouTube 频道仅支持频道 ID、频道链接、@handle 链接或官方 RSS 链接。")

    def _resolve_channel_id_from_page(self, url: str) -> str:
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except RequestException as exc:
            raise self._request_error("解析 YouTube 频道页面", exc) from exc

        final_url = response.url
        final_match = re.search(r"/channel/(UC[\w-]+)", final_url)
        if final_match:
            return final_match.group(1)

        rss_match = re.search(r'feeds/videos\.xml\?channel_id=(UC[\w-]+)', response.text)
        if rss_match:
            return rss_match.group(1)

        canonical_match = re.search(r'https://www\.youtube\.com/channel/(UC[\w-]+)', response.text)
        if canonical_match:
            return canonical_match.group(1)

        patterns = [
            r'"channelId":"(UC[\w-]+)"',
            r'"externalId":"(UC[\w-]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, response.text)
            if match:
                return match.group(1)

        raise YouTubeClientError("无法从该 YouTube 链接解析频道 ID，请改用频道 ID 或官方 RSS 链接。")

    def _request_error(self, action: str, exc: RequestException) -> YouTubeClientError:
        response = getattr(exc, "response", None)
        if response is not None:
            status_code = response.status_code
            if status_code == 404:
                return YouTubeClientError(f"{action}失败：404 页面不存在。")
            return YouTubeClientError(f"{action}失败：HTTP {status_code}。")
        return YouTubeClientError(f"{action}失败：{exc}")

    @staticmethod
    def _is_channel_id(value: str) -> bool:
        return bool(re.fullmatch(r"UC[\w-]{20,}", value))

    @staticmethod
    def _normalize_channel_url(url: str) -> str:
        parsed = urlparse(url)
        if "youtube.com" not in (parsed.netloc or ""):
            return url

        path = parsed.path.rstrip("/")
        if re.fullmatch(r"/@[^/]+", path):
            path = f"{path}/videos"
        return parsed._replace(path=path or "/", fragment="").geturl()

    @staticmethod
    def _parse_published_at(value: str) -> int:
        if not value:
            return 0
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
