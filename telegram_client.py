from __future__ import annotations

import requests


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 20) -> None:
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = timeout

    def send_message(self, text: str) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
            data={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "false",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()

        payload = response.json()
        if not payload.get("ok"):
            raise TelegramError(payload.get("description", "Telegram 发送失败"))

    def send_test_message(self) -> None:
        self.send_message("PushToBilibili 测试消息：Telegram 连接正常。")

def build_batch_video_message(items: list[str]) -> str:
    return "\n".join(item.strip() for item in items if item.strip())
