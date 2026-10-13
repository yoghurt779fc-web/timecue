"""Таймкод: частоты кадров, перевод кадры <-> ЧЧ:ММ:СС:КК (включая drop-frame)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameRate:
    key: str          # идентификатор для файла шоу
    label: str        # что видит пользователь
    nominal: int      # номинальное число кадров в секунде (24/25/30)
    rate: float       # реальная скорость, кадров в секунду
    drop: bool        # drop-frame
    mtc_code: int     # код частоты в MTC (0=24, 1=25, 2=29.97DF, 3=30)


FRAME_RATES: list[FrameRate] = [
    FrameRate("24", "24 к/с (кино)", 24, 24.0, False, 0),
    FrameRate("25", "25 к/с (PAL, стандарт для РФ/Европы)", 25, 25.0, False, 1),
    FrameRate("29.97df", "29.97 к/с drop-frame (NTSC)", 30, 30000 / 1001, True, 2),
    FrameRate("29.97", "29.97 к/с non-drop", 30, 30000 / 1001, False, 3),
    FrameRate("30", "30 к/с", 30, 30.0, False, 3),
]

RATE_BY_KEY = {r.key: r for r in FRAME_RATES}
DEFAULT_RATE = RATE_BY_KEY["25"]


def rate_from_key(key: str) -> FrameRate:
    return RATE_BY_KEY.get(key, DEFAULT_RATE)


def rate_from_mtc_code(code: int, prefer: FrameRate | None = None) -> FrameRate:
    """MTC не отличает 29.97 NDF от 30 — если пользователь выбрал одну из них, оставляем её."""
    if prefer is not None and prefer.mtc_code == code:
        return prefer
    return {0: RATE_BY_KEY["24"], 1: RATE_BY_KEY["25"], 2: RATE_BY_KEY["29.97df"], 3: RATE_BY_KEY["30"]}[code & 3]


def frames_per_day(fr: FrameRate) -> int:
    if fr.drop:
        return 24 * 6 * 17982
    return 24 * 3600 * fr.nominal


def tc_to_frames(h: int, m: int, s: int, f: int, fr: FrameRate) -> int:
    base = fr.nominal
    total = ((h * 60 + m) * 60 + s) * base + f
    if fr.drop:
        minutes = h * 60 + m
        total -= 2 * (minutes - minutes // 10)
    return total


def frames_to_tc(n: int, fr: FrameRate) -> tuple[int, int, int, int]:
    n = int(n) % frames_per_day(fr)
    if fr.drop:
        d, m = divmod(n, 17982)
        if m > 1:
            n += 18 * d + 2 * ((m - 2) // 1798)
        else:
            n += 18 * d
    base = fr.nominal
    f = n % base
    s = (n // base) % 60
    mi = (n // (base * 60)) % 60
    h = (n // (base * 3600)) % 24
    return h, mi, s, f


def format_tc(frames: float, fr: FrameRate) -> str:
    h, m, s, f = frames_to_tc(int(frames), fr)
    sep = ";" if fr.drop else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"


def parse_tc(text: str, fr: FrameRate) -> int | None:
    """Разбирает ввод пользователя в номер кадра.

    '01:02:03:04' — ЧЧ:ММ:СС:КК, '1:02:03' — ЧЧ:ММ:СС, '2:30' — ММ:СС, '90' — секунды.
    Разделителем может быть ':' ';' '.' ','. Возвращает None, если ввод некорректен.
    """
    if text is None:
        return None
    t = text.strip().replace(";", ":").replace(".", ":").replace(",", ":")
    if not t:
        return None
    parts = t.split(":")
    try:
        nums = [int(p) if p.strip() else 0 for p in parts]
    except ValueError:
        return None
    if any(v < 0 for v in nums) or len(nums) > 4:
        return None
    if len(nums) == 1:
        h, m, s, f = 0, 0, nums[0], 0
    elif len(nums) == 2:
        h, m, s, f = 0, nums[0], nums[1], 0
    elif len(nums) == 3:
        h, m, s, f = nums[0], nums[1], nums[2], 0
    else:
        h, m, s, f = nums
    if f >= fr.nominal or s >= 60 or m >= 60 or h >= 24:
        # Допускаем "переполненные" секунды/минуты только в коротких формах (90 -> 00:01:30)
        if len(nums) <= 2 and f < fr.nominal:
            total_s = h * 3600 + m * 60 + s
            h, rem = divmod(total_s, 3600)
            m, s = divmod(rem, 60)
            if h >= 24:
                return None
        else:
            return None
    if fr.drop and s == 0 and f < 2 and m % 10 != 0:
        f = 2  # таких кадров в drop-frame не существует
    return tc_to_frames(h, m, s, f, fr)
