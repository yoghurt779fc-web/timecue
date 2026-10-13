"""Файл шоу: список кью и все настройки. Сохраняется в JSON (*.tcshow)."""

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass, field

from .dmx import DmxSettings, parse_channel_spec
from .osc import OscTarget

CUE_TYPES = {
    "audio": "Звук",
    "dmx": "DMX",
    "osc": "OSC",
}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class DmxRow:
    universe: int = 1
    channels: str = "1"
    value: int = 255

    def to_dict(self) -> dict:
        return dict(universe=self.universe, channels=self.channels, value=self.value)

    @staticmethod
    def from_dict(d: dict) -> "DmxRow":
        return DmxRow(int(d.get("universe", 1)), str(d.get("channels", "1")), int(d.get("value", 255)))

    def expand(self) -> list[tuple[int, int, int]]:
        try:
            chans = parse_channel_spec(self.channels)
        except ValueError:
            chans = []
        return [(self.universe, c, self.value) for c in chans]


@dataclass
class Cue:
    id: str = field(default_factory=new_id)
    type: str = "osc"
    number: str = ""
    name: str = ""
    trigger: str = ""            # таймкод срабатывания, пусто — только по GO
    enabled: bool = True
    notes: str = ""
    # звук
    file: str = ""
    volume_db: float = 0.0
    out_pair: int = 1            # 1 = каналы 1-2, 2 = 3-4 ...
    # DMX
    dmx_rows: list[DmxRow] = field(default_factory=list)
    dmx_clear: bool = False
    # OSC
    osc_target: str = "Основной"
    osc_address: str = "/cue/1"
    osc_args: str = ""

    def to_dict(self) -> dict:
        d = dict(id=self.id, type=self.type, number=self.number, name=self.name, trigger=self.trigger,
                 enabled=self.enabled, notes=self.notes)
        if self.type == "audio":
            d.update(file=self.file, volume_db=self.volume_db, out_pair=self.out_pair)
        elif self.type == "dmx":
            d.update(dmx_rows=[r.to_dict() for r in self.dmx_rows], dmx_clear=self.dmx_clear)
        elif self.type == "osc":
            d.update(osc_target=self.osc_target, osc_address=self.osc_address, osc_args=self.osc_args)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Cue":
        c = Cue()
        for k in ("id", "type", "number", "name", "trigger", "notes", "file", "osc_target", "osc_address", "osc_args"):
            if k in d and d[k] is not None:
                setattr(c, k, str(d[k]))
        c.enabled = bool(d.get("enabled", True))
        c.volume_db = float(d.get("volume_db", 0.0))
        c.out_pair = int(d.get("out_pair", 1))
        c.dmx_clear = bool(d.get("dmx_clear", False))
        c.dmx_rows = [DmxRow.from_dict(r) for r in d.get("dmx_rows", [])]
        if c.type not in CUE_TYPES:
            c.type = "osc"
        return c

    def summary(self) -> str:
        if self.type == "audio":
            name = os.path.basename(self.file) if self.file else "файл не выбран"
            vol = f", {self.volume_db:+.1f} дБ" if abs(self.volume_db) > 0.05 else ""
            pair = f", вых. {2 * self.out_pair - 1}-{2 * self.out_pair}" if self.out_pair != 1 else ""
            return f"{name}{vol}{pair}"
        if self.type == "dmx":
            parts = [f"U{r.universe}:{r.channels}={r.value}" for r in self.dmx_rows[:4]]
            if len(self.dmx_rows) > 4:
                parts.append(f"… ещё {len(self.dmx_rows) - 4}")
            txt = ", ".join(parts) if parts else "нет каналов"
            return ("обнулить всё → " if self.dmx_clear else "") + txt
        return f"{self.osc_target} {self.osc_address} {self.osc_args}".strip()


def default_settings() -> dict:
    return {
        "timecode": {
            "fps": "25",
            "source": "internal",       # internal | mtc | ltc
            "start_tc": "00:00:00:00",
            "freewheel_frames": 10,
            "chase_offset_frames": 0,
            "dmx_tracking": True,
        },
        "mtc": {
            "out_enabled": False,
            "out_port": "",
            "in_port": "",
        },
        "ltc": {
            "out_enabled": False,
            "out_device": "",
            "out_channel": 2,
            "out_level_db": -10.0,
            "out_offset_ms": 0.0,
            "in_device": "",
            "in_channel": 1,
        },
        "audio": {
            "device": "",
            "channels": 2,
            "sample_rate": 48000,
            "blocksize": 256,
        },
        "dmx": DmxSettings().to_dict(),
        "osc": {
            "targets": [OscTarget().to_dict()],
        },
    }


def merge_settings(loaded: dict | None) -> dict:
    base = default_settings()
    if not isinstance(loaded, dict):
        return base
    for section, values in loaded.items():
        if section in base and isinstance(base[section], dict) and isinstance(values, dict):
            base[section].update(values)
    return base


class Show:
    def __init__(self):
        self.cues: list[Cue] = []
        self.settings: dict = default_settings()
        self.path: str = ""
        self.modified = False

    def to_dict(self) -> dict:
        return {"format": "timecue-show", "version": 1, "settings": self.settings,
                "cues": [c.to_dict() for c in self.cues]}

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        self.path = path
        self.modified = False

    @staticmethod
    def load(path: str) -> "Show":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or d.get("format") != "timecue-show":
            raise ValueError("это не файл шоу TimeCue")
        s = Show()
        s.settings = merge_settings(d.get("settings"))
        s.cues = [Cue.from_dict(c) for c in d.get("cues", [])]
        s.path = path
        # относительные пути к звуку считаем от папки шоу
        base = os.path.dirname(os.path.abspath(path))
        for c in s.cues:
            if c.file and not os.path.isabs(c.file):
                c.file = os.path.normpath(os.path.join(base, c.file))
        return s

    def settings_copy(self) -> dict:
        return copy.deepcopy(self.settings)

    def next_number(self) -> str:
        best = 0
        for c in self.cues:
            try:
                best = max(best, int(float(c.number)))
            except ValueError:
                pass
        return str(best + 1)
