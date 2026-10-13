"""MIDI Time Code: генерация quarter-frame / full-frame сообщений и их разбор."""

from __future__ import annotations

from .tc import FrameRate, frames_to_tc


def quarter_frame_bytes(piece: int, h: int, m: int, s: int, f: int, rate_code: int) -> list[int]:
    if piece == 0:
        nib = f & 0xF
    elif piece == 1:
        nib = (f >> 4) & 0x1
    elif piece == 2:
        nib = s & 0xF
    elif piece == 3:
        nib = (s >> 4) & 0x3
    elif piece == 4:
        nib = m & 0xF
    elif piece == 5:
        nib = (m >> 4) & 0x3
    elif piece == 6:
        nib = h & 0xF
    else:
        nib = ((h >> 4) & 0x1) | ((rate_code & 0x3) << 1)
    return [0xF1, ((piece & 7) << 4) | nib]


def full_frame_sysex(h: int, m: int, s: int, f: int, rate_code: int) -> list[int]:
    return [0xF0, 0x7F, 0x7F, 0x01, 0x01, ((rate_code & 3) << 5) | (h & 0x1F), m & 0x3F, s & 0x3F, f & 0x1F, 0xF7]


class MTCSequence:
    """Выдаёт 8 quarter-frame сообщений на каждые 2 кадра.

    next_message(frame_number) — вызывается каждые 1/(4*fps) секунды; номер кадра
    фиксируется при отправке нулевой четверти, как требует стандарт.
    """

    def __init__(self, fr: FrameRate):
        self.fr = fr
        self.piece = 0
        self._latched = (0, 0, 0, 0)

    def restart(self) -> None:
        self.piece = 0

    def next_message(self, frame_number: int) -> list[int]:
        if self.piece == 0:
            self._latched = frames_to_tc(frame_number, self.fr)
        h, m, s, f = self._latched
        msg = quarter_frame_bytes(self.piece, h, m, s, f, self.fr.mtc_code)
        self.piece = (self.piece + 1) % 8
        return msg


class MTCParser:
    """Собирает таймкод из входящих MTC-сообщений.

    feed(bytes) возвращает None или (h, m, s, f, rate_code, kind), где kind:
    'qf' — собран полный набор четвертей (время уже скорректировано на +2 кадра),
    'full' — пришёл full-frame (позиционирование, обычно при остановке).
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._nibbles = [0] * 8
        self._expected = 0
        self._count = 0

    def feed(self, data) -> tuple | None:
        data = list(data)
        if not data:
            return None
        if data[0] == 0xF1 and len(data) >= 2:
            piece = (data[1] >> 4) & 7
            nib = data[1] & 0xF
            if piece == 0:
                self._count = 0
                self._expected = 0
            if piece != self._expected:
                self._count = 0
                self._expected = 0
                return None
            self._nibbles[piece] = nib
            self._count += 1
            self._expected = (piece + 1) % 8
            if piece == 7 and self._count == 8:
                n = self._nibbles
                f = n[0] | ((n[1] & 1) << 4)
                s = n[2] | ((n[3] & 3) << 4)
                m = n[4] | ((n[5] & 3) << 4)
                h = n[6] | ((n[7] & 1) << 4)
                rate = (n[7] >> 1) & 3
                self._count = 0
                return h, m, s, f, rate, "qf"
            return None
        if len(data) >= 10 and data[0] == 0xF0 and data[1] == 0x7F and data[3] == 0x01 and data[4] == 0x01:
            hr = data[5]
            rate = (hr >> 5) & 3
            self.reset()
            return hr & 0x1F, data[6] & 0x3F, data[7] & 0x3F, data[8] & 0x1F, rate, "full"
        return None
