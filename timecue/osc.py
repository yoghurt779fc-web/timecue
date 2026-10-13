"""OSC: кодирование сообщений и отправка по UDP (без внешних библиотек)."""

from __future__ import annotations

import shlex
import socket
import struct
from dataclasses import dataclass


def _pad(b: bytes) -> bytes:
    return b + b"\x00" * (4 - len(b) % 4)


def _osc_string(s: str) -> bytes:
    return _pad(s.encode("utf-8"))


def parse_args(text: str) -> list:
    """Строка аргументов -> список значений.

    1 → int, 0.5 → float, true/false → bool, "текст с пробелами" → строка.
    Принудительный тип: i:5, f:5, s:5, T, F.
    """
    out: list = []
    if not text or not text.strip():
        return out
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    for tok in tokens:
        low = tok.lower()
        if low.startswith("i:"):
            out.append(int(float(tok[2:])))
        elif low.startswith("f:"):
            out.append(float(tok[2:]))
        elif low.startswith("s:"):
            out.append(tok[2:])
        elif low in ("true", "t"):
            out.append(True)
        elif low in ("false", "f"):
            out.append(False)
        else:
            try:
                out.append(int(tok))
            except ValueError:
                try:
                    out.append(float(tok.replace(",", ".")) if any(c.isdigit() for c in tok) else tok)
                except ValueError:
                    out.append(tok)
    return out


def encode_message(address: str, args: list) -> bytes:
    if not address.startswith("/"):
        address = "/" + address
    tags = ","
    payload = b""
    for a in args:
        if isinstance(a, bool):
            tags += "T" if a else "F"
        elif isinstance(a, int):
            tags += "i"
            payload += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            payload += struct.pack(">f", a)
        else:
            tags += "s"
            payload += _osc_string(str(a))
    return _osc_string(address) + _osc_string(tags) + payload


@dataclass
class OscTarget:
    name: str = "Основной"
    host: str = "127.0.0.1"
    port: int = 8000

    def to_dict(self) -> dict:
        return dict(name=self.name, host=self.host, port=self.port)

    @staticmethod
    def from_dict(d: dict) -> "OscTarget":
        return OscTarget(str(d.get("name", "Основной")), str(d.get("host", "127.0.0.1")), int(d.get("port", 8000)))


class OscSender:
    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sent = 0

    def send(self, host: str, port: int, address: str, args: list) -> None:
        self._sock.sendto(encode_message(address, args), (host, int(port)))
        self.sent += 1
