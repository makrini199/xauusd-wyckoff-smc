"""Envío de mensajes y gráficos a Telegram con la Bot API (sin dependencias).

Configuración por variables de entorno:
    TELEGRAM_TOKEN    token del bot (de @BotFather)
    TELEGRAM_CHAT_ID  id del chat al que se envían las alertas
"""
import json
import os
import uuid
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, timeout: float = 20):
        self.token = token or os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.timeout = timeout
        if not self.token or not self.chat_id:
            raise TelegramError("faltan TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID")

    def _call(self, method: str, data: bytes, content_type: str) -> dict:
        req = urllib.request.Request(API.format(token=self.token, method=method), data=data,
                                     headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise TelegramError(f"{method}: HTTP {e.code} {e.read().decode(errors='replace')}") from e
        if not out.get("ok"):
            raise TelegramError(f"{method}: {out}")
        return out

    def send_message(self, text: str) -> dict:
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        return self._call("sendMessage", data, "application/x-www-form-urlencoded")

    def send_photo(self, path: str | Path, caption: str = "") -> dict:
        """Envía una imagen con pie de foto (Telegram admite hasta 1024 caracteres)."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("chat_id", self.chat_id), ("caption", caption[:1024]), ("parse_mode", "HTML")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        img = Path(path).read_bytes()
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                     f'filename="{Path(path).name}"\r\nContent-Type: image/png\r\n\r\n'.encode() + img + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return self._call("sendPhoto", b"".join(parts), f"multipart/form-data; boundary={boundary}")


class ConsoleSink:
    """Sustituto de Telegram para `--simular`: imprime las alertas en pantalla."""

    def send_message(self, text: str):
        print("\n" + _strip_html(text))

    def send_photo(self, path, caption: str = ""):
        print("\n" + _strip_html(caption) + f"\n[gráfico: {path}]")


def _strip_html(s: str) -> str:
    import re
    return re.sub(r"</?[a-z]+>", "", s)
