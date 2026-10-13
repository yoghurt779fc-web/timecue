"""LTC (SMPTE линейный таймкод): генерация аудиосигнала и декодирование со входа.

Формат кадра — 80 бит, биты полей идут младшим вперёд, в конце синхрослово
0011 1111 1111 1101. Кодирование bi-phase mark: смена уровня в начале каждого
бита и дополнительная смена в середине бита, если бит = 1.
"""

from __future__ import annotations

import numpy as np

from .tc import FrameRate, frames_to_tc

SYNC_BITS = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]


def _put(bits: list[int], start: int, value: int, count: int) -> None:
    for i in range(count):
        bits[start + i] = (value >> i) & 1


def _get(bits, start: int, count: int) -> int:
    v = 0
    for i in range(count):
        v |= (bits[start + i] & 1) << i
    return v


def encode_frame_bits(h: int, m: int, s: int, f: int, fr: FrameRate, user_bits: int = 0) -> list[int]:
    bits = [0] * 80
    _put(bits, 0, f % 10, 4)
    _put(bits, 8, f // 10, 2)
    bits[10] = 1 if fr.drop else 0
    _put(bits, 16, s % 10, 4)
    _put(bits, 24, s // 10, 3)
    _put(bits, 32, m % 10, 4)
    _put(bits, 40, m // 10, 3)
    _put(bits, 48, h % 10, 4)
    _put(bits, 56, h // 10, 2)
    # пользовательские биты (8 групп по 4 бита)
    for g, pos in enumerate((4, 12, 20, 28, 36, 44, 52, 60)):
        _put(bits, pos, (user_bits >> (4 * g)) & 0xF, 4)
    bits[64:80] = SYNC_BITS
    # бит коррекции полярности: делаем число единиц в кадре чётным,
    # тогда каждый кадр начинается с одинаковой фазы
    parity_pos = 59 if fr.nominal == 25 else 27
    bits[parity_pos] = 0
    if sum(bits) % 2:
        bits[parity_pos] = 1
    return bits


def decode_frame_bits(bits) -> tuple[int, int, int, int, bool]:
    f = _get(bits, 0, 4) + 10 * _get(bits, 8, 2)
    df = bool(bits[10])
    s = _get(bits, 16, 4) + 10 * _get(bits, 24, 3)
    m = _get(bits, 32, 4) + 10 * _get(bits, 40, 3)
    h = _get(bits, 48, 4) + 10 * _get(bits, 56, 2)
    return h, m, s, f, df


class LTCGenerator:
    """Потоковый генератор LTC. read(n) отдаёт n сэмплов float32.

    frame_source() вызывается в начале каждого кадра и должна вернуть номер
    кадра (int) или None — тогда выдаётся тишина длиной в кадр.
    """

    def __init__(self, sample_rate: int, fr: FrameRate, frame_source, level_db: float = -10.0):
        self.sample_rate = int(sample_rate)
        self.fr = fr
        self.frame_source = frame_source
        self.set_level_db(level_db)
        self._buf = np.zeros(0, dtype=np.float32)
        self._sample_clock = 0.0     # дробная позиция в сэмплах, чтобы 29.97 не «уплывало»
        self._polarity = 1
        self._smooth_prev = 0.0
        self.active = False          # выдавался ли сигнал в последнем кадре

    def set_level_db(self, level_db: float) -> None:
        self.amplitude = float(10 ** (level_db / 20.0))

    def _render_frame(self) -> np.ndarray:
        frame_len = self.sample_rate / self.fr.rate
        start = self._sample_clock
        end = start + frame_len
        n = int(np.floor(end)) - int(np.floor(start))
        self._sample_clock = end
        if self._sample_clock > 1e9:
            self._sample_clock -= np.floor(self._sample_clock)
        number = self.frame_source()
        if number is None:
            self.active = False
            return np.zeros(n, dtype=np.float32)
        self.active = True
        h, m, s, f = frames_to_tc(int(number), self.fr)
        bits = np.array(encode_frame_bits(h, m, s, f, self.fr), dtype=np.int64)
        bit_len = frame_len / 80.0
        # позиции центров сэмплов относительно начала кадра
        x = (np.arange(n) + (np.floor(start) - start) + 0.5) / bit_len
        x = np.clip(x, 0, 79.999)
        bi = x.astype(np.int64)
        half = (x - bi) >= 0.5
        cum = np.concatenate(([0], np.cumsum(1 + bits)))
        toggles = cum[bi] + 1 + (half & (bits[bi] == 1))
        level = np.where(toggles % 2 == 0, 1.0, -1.0) * self._polarity
        # после кадра полярность сохраняется (число смен чётное), но держим явно
        total = int(cum[80])
        if total % 2:
            self._polarity = -self._polarity
        return level.astype(np.float32)

    def read(self, n: int) -> np.ndarray:
        while self._buf.size < n:
            self._buf = np.concatenate((self._buf, self._render_frame()))
        out = self._buf[:n]
        self._buf = self._buf[n:]
        # мягкие фронты (время нарастания по стандарту ~25 мкс): усреднение соседних сэмплов
        padded = np.concatenate(([self._smooth_prev], out))
        sm = 0.5 * padded[1:] + 0.5 * padded[:-1]
        if out.size:
            self._smooth_prev = float(out[-1])
        return (sm * self.amplitude).astype(np.float32)


class LTCDecoder:
    """Потоковый декодер LTC. feed(samples) возвращает список
    (h, m, s, f, drop_flag, sample_offset_in_block) для каждого найденного кадра."""

    def __init__(self, sample_rate: int):
        self.sample_rate = int(sample_rate)
        self.reset()

    def reset(self) -> None:
        self._state = 0
        self._last_edge = None       # абсолютный индекс последней смены уровня
        self._abs = 0                # абсолютный счётчик сэмплов
        self._bit_len = self.sample_rate / (27.0 * 80.0)
        self._half_pending = False
        self._reg = 0
        self._nbits = 0
        self._peak = 0.05
        self.max_frame_seen = 0
        self._frames_since_reset = 0

    @property
    def detected_nominal(self) -> int | None:
        if self._frames_since_reset < 30:
            return None
        mf = self.max_frame_seen
        if mf >= 29:
            return 30
        if mf >= 24:
            return 25
        if mf >= 23:
            return 24
        return None

    def feed(self, samples: np.ndarray) -> list[tuple]:
        x = np.asarray(samples, dtype=np.float32)
        if x.ndim > 1:
            x = x[:, 0]
        n = x.size
        results: list[tuple] = []
        if n == 0:
            return results
        peak = float(np.max(np.abs(x)))
        self._peak = max(self._peak * 0.9, peak) if peak > 0.002 else self._peak * 0.9
        thr = max(0.25 * self._peak, 0.003)
        s = np.zeros(n, dtype=np.int8)
        s[x > thr] = 1
        s[x < -thr] = -1
        nz = s != 0
        idx = np.where(nz, np.arange(n), -1)
        idx = np.maximum.accumulate(idx)
        filled = np.where(idx >= 0, s[np.maximum(idx, 0)], self._state).astype(np.int8)
        prev = np.concatenate(([self._state], filled[:-1]))
        edges = np.nonzero((filled != prev) & (filled != 0) & (prev != 0))[0]
        if filled.size:
            self._state = int(filled[-1]) if filled[-1] != 0 else self._state
        base = self._abs
        for e in edges:
            pos = base + int(e)
            if self._last_edge is None:
                self._last_edge = pos
                continue
            d = pos - self._last_edge
            self._last_edge = pos
            B = self._bit_len
            if d > 1.6 * B or d < 0.3 * B:
                # пропуск или помеха: сбрасываем сборку кадра; если интервал похож
                # на целый бит другой частоты кадров — подстраиваем оценку длины бита
                self._half_pending = False
                self._nbits = 0
                if self.sample_rate / (31 * 80) * 0.8 <= d <= self.sample_rate / (23 * 80) * 1.25:
                    self._bit_len = 0.5 * self._bit_len + 0.5 * d
                continue
            if d < 0.75 * B:
                self._bit_len = 0.95 * B + 0.05 * (2 * d)
                if self._half_pending:
                    self._half_pending = False
                    bit = 1
                else:
                    self._half_pending = True
                    continue
            else:
                self._bit_len = 0.95 * B + 0.05 * d
                if self._half_pending:
                    # рассинхрон по полубитам — сбрасываем
                    self._half_pending = False
                    self._nbits = 0
                bit = 0
            self._push_bit(bit, pos - base, results)
        self._abs += n
        return results

    _SYNC_VALUE = sum(b << (15 - i) for i, b in enumerate(SYNC_BITS))
    _MASK80 = (1 << 80) - 1

    def _push_bit(self, bit: int, offset: int, results: list) -> None:
        self._reg = ((self._reg << 1) | bit) & self._MASK80
        self._nbits = min(self._nbits + 1, 200)
        if self._nbits >= 80 and (self._reg & 0xFFFF) == self._SYNC_VALUE:
            bits = [(self._reg >> (79 - j)) & 1 for j in range(80)]
            h, m, s, f, df = decode_frame_bits(bits)
            if h < 24 and m < 60 and s < 60 and f < 30:
                self.max_frame_seen = max(self.max_frame_seen, f)
                self._frames_since_reset += 1
                results.append((h, m, s, f, df, offset))
