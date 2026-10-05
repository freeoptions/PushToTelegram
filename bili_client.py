from __future__ import annotations

import hashlib
import time
import urllib.parse
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path

import browser_cookie3
import requests
from shadowcopy.exceptions import RequiresAdminError


MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_SYNC_PAGES = 10


class BiliClientError(RuntimeError):
    pass


class BiliRiskControlError(BiliClientError):
    pass


class BrowserCookieReadError(RuntimeError):
    pass


@dataclass(slots=True)
class VideoItem:
    uid: str
    up_name: str
    bvid: str
    title: str
    video_url: str
    published_at: int

    @property
    def publish_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.published_at))


class BiliClient:
    def __init__(self, cookie: str = "", use_browser_cookie: bool = True, timeout: int = 20) -> None:
        self._session = requests.Session()
        # B 站本身不需要代理，本机代理不稳定时会导致 SSL EOF 等连接中断。
        self._session.trust_env = False
        self._session.proxies.clear()
        self._session.headers.update(DEFAULT_HEADERS)
        self._timeout = timeout
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_key_at = 0.0

        if use_browser_cookie:
            self._load_browser_cookies()
        if cookie.strip():
            self._load_cookie_string(cookie.strip())

        self._bootstrap_cookies()

    def fetch_recent_videos(self, uid: str, count: int = 10) -> list[VideoItem]:
        uid = uid.strip()
        if not uid.isdigit():
            raise BiliClientError(f"UID 不合法：{uid}")

        videos, _total_count = self._fetch_video_page(
            uid,
            page=1,
            page_size=max(1, min(count, 30)),
        )
        return videos

    def fetch_videos_since(
        self,
        uid: str,
        since_timestamp: int | None,
        *,
        stop_bvids: set[str] | None = None,
        page_delay: float = 0.0,
    ) -> tuple[list[VideoItem], bool]:
        """Fetch videos newer than a checkpoint, walking pages until the boundary."""
        uid = uid.strip()
        if not uid.isdigit():
            raise BiliClientError(f"UID 不合法：{uid}")

        page = 1
        page_size = 30
        videos: list[VideoItem] = []
        seen_page_signatures: set[tuple[str, ...]] = set()
        known_bvids = stop_bvids or set()
        scan_complete = False

        for _page_index in range(MAX_SYNC_PAGES):
            page_videos, total_count = self._fetch_video_page(
                uid,
                page=page,
                page_size=page_size,
            )
            if not page_videos:
                scan_complete = True
                break

            page_signature = tuple(video.bvid for video in page_videos)
            if page_signature in seen_page_signatures:
                break
            seen_page_signatures.add(page_signature)

            boundary_reached = False
            for video in page_videos:
                if video.bvid in known_bvids:
                    boundary_reached = True
                    break
                if since_timestamp is not None and video.published_at < since_timestamp:
                    boundary_reached = True
                    break
                videos.append(video)

            if boundary_reached:
                scan_complete = True
                break

            reached_last_page = len(page_videos) < page_size
            if total_count > 0 and page * page_size >= total_count:
                reached_last_page = True
            if reached_last_page:
                scan_complete = True
                break

            page += 1
            if page_delay > 0:
                time.sleep(page_delay)

        return videos, scan_complete

    def _fetch_video_page(
        self,
        uid: str,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[VideoItem], int]:
        params = {
            "mid": uid,
            "pn": str(max(1, page)),
            "ps": str(max(1, min(page_size, 30))),
            "order": "pubdate",
            "tid": "0",
            "platform": "web",
            "web_location": "1550101",
            "order_avoided": "true",
        }
        signed_params = self._sign_wbi(params)
        response = self._session.get(
            "https://api.bilibili.com/x/space/wbi/arc/search",
            params=signed_params,
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        code = int(payload.get("code", -1))
        if code != 0:
            message = self._friendly_error(code, str(payload.get("message", "未知错误")))
            if code in (-352, -412):
                raise BiliRiskControlError(message)
            raise BiliClientError(message)

        data = payload.get("data") or {}
        page_info = data.get("page") or {}
        total_count = int(page_info.get("count", 0) or 0)
        vlist = ((data.get("list") or {}).get("vlist")) or []
        videos: list[VideoItem] = []
        for item in vlist:
            bvid = str(item.get("bvid", "")).strip()
            if not bvid:
                continue
            up_name = str(item.get("author", "")).strip() or str(item.get("name", "")).strip() or uid
            videos.append(
                VideoItem(
                    uid=uid,
                    up_name=up_name,
                    bvid=bvid,
                    title=str(item.get("title", "")).strip() or bvid,
                    video_url=f"https://www.bilibili.com/video/{bvid}",
                    published_at=int(item.get("created", 0) or 0),
                )
            )
        return videos, total_count

    def _sign_wbi(self, params: dict[str, str]) -> dict[str, str]:
        img_key, sub_key = self._get_wbi_keys()
        mixin_key = self._get_mixin_key(img_key + sub_key)
        signed_params = {key: str(value) for key, value in params.items()}
        signed_params["wts"] = str(int(time.time()))
        sorted_params = {
            key: "".join(ch for ch in value if ch not in "!'()*")
            for key, value in sorted(signed_params.items(), key=lambda item: item[0])
        }
        query = "&".join(
            f"{urllib.parse.quote(key, safe='')}={urllib.parse.quote(value, safe='')}"
            for key, value in sorted_params.items()
        )
        sorted_params["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
        return sorted_params

    def _bootstrap_cookies(self) -> None:
        try:
            self._get_with_retry("https://www.bilibili.com/", retry_count=1)
        except requests.RequestException:
            pass

        try:
            self._ensure_cookie("buvid3", self._fetch_buvid3())
        except (requests.RequestException, ValueError):
            pass

        try:
            spi_response = self._get_with_retry(
                "https://api.bilibili.com/x/frontend/finger/spi",
                retry_count=1,
            )
            spi_response.raise_for_status()
            spi_payload = spi_response.json()
        except (requests.RequestException, ValueError):
            return

        if int(spi_payload.get("code", -1)) == 0:
            spi_data = spi_payload.get("data") or {}
            self._ensure_cookie("buvid3", str(spi_data.get("b_3", "")).strip())
            self._ensure_cookie("buvid4", str(spi_data.get("b_4", "")).strip())

    def _fetch_buvid3(self) -> str:
        response = self._get_with_retry(
            "https://api.bilibili.com/x/web-frontend/getbuvid",
            retry_count=1,
            retry_delay=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code", -1)) != 0:
            return ""
        return str((payload.get("data") or {}).get("buvid", "")).strip()

    def _get_with_retry(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        retry_count: int = 1,
        retry_delay: float = 5.0,
    ) -> requests.Response:
        for attempt in range(retry_count + 1):
            try:
                return self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException:
                if attempt >= retry_count:
                    raise
                time.sleep(retry_delay)

        raise BiliClientError("B 站请求重试失败")

    def _ensure_cookie(self, name: str, value: str) -> None:
        if value:
            self._session.cookies.set(name, value, domain=".bilibili.com")

    def _load_browser_cookies(self) -> None:
        for loader in (browser_cookie3.edge, browser_cookie3.chrome, browser_cookie3.brave):
            try:
                cookie_jar = loader(domain_name="bilibili.com")
            except Exception:
                continue
            if self._load_cookie_jar(cookie_jar):
                break

        for cookie_file, key_file in _iter_cent_browser_sources():
            try:
                cookie_jar = browser_cookie3.chrome(
                    cookie_file=str(cookie_file),
                    key_file=str(key_file),
                    domain_name="bilibili.com",
                )
            except Exception:
                continue
            if self._load_cookie_jar(cookie_jar):
                break

    def _load_cookie_jar(self, cookie_jar) -> bool:
        matched = False
        for cookie in cookie_jar:
            if "bilibili.com" not in (cookie.domain or ""):
                continue
            self._session.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain or ".bilibili.com",
                path=cookie.path or "/",
            )
            matched = True
        return matched

    def _load_cookie_string(self, cookie_header: str) -> None:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        for name, morsel in cookie.items():
            self._session.cookies.set(
                name,
                morsel.value,
                domain=".bilibili.com",
                path="/",
            )

    def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys and (time.time() - self._wbi_key_at) < 3600:
            return self._wbi_keys

        response = self._session.get(
            "https://api.bilibili.com/x/web-interface/nav",
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        code = int(payload.get("code", -1))
        if code not in (0, -101):
            raise BiliClientError(f"获取 WBI key 失败：{payload.get('message', '未知错误')}")

        wbi_img = (payload.get("data") or {}).get("wbi_img") or {}
        img_url = str(wbi_img.get("img_url", "")).strip()
        sub_url = str(wbi_img.get("sub_url", "")).strip()
        if not img_url or not sub_url:
            raise BiliClientError("获取 WBI key 失败：返回内容不完整")

        img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
        self._wbi_keys = (img_key, sub_key)
        self._wbi_key_at = time.time()
        return self._wbi_keys

    @staticmethod
    def _get_mixin_key(raw: str) -> str:
        return "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]

    @staticmethod
    def _friendly_error(code: int, message: str) -> str:
        lower_message = message.lower()
        if code in (-101, -400) or "账号未登录" in message or "请先登录" in message or "not login" in lower_message:
            return "B站 Cookie 已失效或登录状态无效，请重新读取浏览器 Cookie 后再试。"
        if code in (-352, -412):
            return f"B 站接口返回风控错误 {code}：{message}。建议重新导入浏览器 Cookie 后再试。"
        return f"B 站接口错误 {code}：{message}"


def export_browser_cookie_header() -> str:
    for loader in (browser_cookie3.edge, browser_cookie3.chrome, browser_cookie3.brave):
        try:
            cookie_jar = loader(domain_name="bilibili.com")
        except Exception:
            continue
        header = _cookie_jar_to_header(cookie_jar)
        if header:
            return header

    cent_errors: list[str] = []
    for cookie_file, key_file in _iter_cent_browser_sources():
        try:
            cookie_jar = browser_cookie3.chrome(
                cookie_file=str(cookie_file),
                key_file=str(key_file),
                domain_name="bilibili.com",
            )
        except RequiresAdminError:
            cent_errors.append(f"百分浏览器的 Cookie 数据库当前被占用：{cookie_file.parent.parent.name}")
            continue
        except Exception as exc:
            cent_errors.append(f"百分浏览器读取失败：{exc}")
            continue

        header = _cookie_jar_to_header(cookie_jar)
        if header:
            return header

    if cent_errors:
        raise BrowserCookieReadError(
            "；".join(cent_errors)
            + "。如果你希望浏览器开着时也能读取，请用管理员权限运行本程序；"
            + "否则请先完全关闭百分浏览器后，再点击一次“读取浏览器 Cookie”。"
        )
    return ""


def _cookie_jar_to_header(cookie_jar) -> str:
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in cookie_jar:
        if "bilibili.com" not in (cookie.domain or ""):
            continue
        if cookie.name in seen:
            continue
        seen.add(cookie.name)
        pairs.append(f"{cookie.name}={cookie.value}")
    return "; ".join(pairs)


def _iter_cent_browser_sources() -> list[tuple[Path, Path]]:
    user_data_dir = Path.home() / "AppData" / "Local" / "CentBrowser" / "User Data"
    local_state = user_data_dir / "Local State"
    if not local_state.exists():
        return []

    sources: list[tuple[Path, Path]] = []
    profile_dirs = [user_data_dir / "Default"]
    profile_dirs.extend(sorted(user_data_dir.glob("Profile *")))
    for profile_dir in profile_dirs:
        cookie_file = profile_dir / "Network" / "Cookies"
        if cookie_file.exists():
            sources.append((cookie_file, local_state))
    return sources
