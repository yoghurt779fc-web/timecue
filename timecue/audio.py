"""Звук: воспроизведение аудиокью, выход LTC на канал звуковой карты, вход LTC."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

try:
    import sounddevice as sd
    AUDIO_IMPORT_ERROR = ""
except Exception as e:  # PortAudio может отсутствовать
    sd = None
    AUDIO_IMPORT_ERROR = str(e)

try:
    import soundfile as sf
except Exception:
    sf = None


def audio_available() -> bool:
    return sd is not None


def _device_label(dev: dict) -> str:
    try:
        api = sd.query_hostapis(dev["hostapi"])["name"]
    except Exception:
        api = ""
    api = api.replace("Windows ", "")
    return f"{dev['name']} [{api}]" if api else dev["name"]


def list_devices(kind: str) -> list[tuple[str, int]]:
    """kind: 'output' | 'input'. Возвращает [(название, число каналов)]."""
    if sd is None:
        return []
    out = []
    key = "max_output_channels" if kind == "output" else "max_input_channels"
    try:
        for dev in sd.query_devices():
            if dev[key] > 0:
                out.append((_device_label(dev), int(dev[key])))
    except Exception:
        return []
    return out


def resolve_device(label: str, kind: str):
    """Название -> индекс sounddevice (None — устройство по умолчанию)."""
    if sd is None or not label:
        return None
    key = "max_output_channels" if kind == "output" else "max_input_channels"
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev[key] > 0 and _device_label(dev) == label:
                return i
    except Exception:
        pass
    return None


def load_audio_file(path: str, sample_rate: int) -> np.ndarray:
    """Читает файл (wav/aiff/flac/ogg/mp3) в стерео float32 нужной частоты."""
    if sf is None:
        raise RuntimeError("библиотека soundfile недоступна")
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    if sr != sample_rate and data.shape[0] > 1:
        n_out = int(round(data.shape[0] * sample_rate / sr))
        t_in = np.arange(data.shape[0], dtype=np.float64)
        t_out = np.linspace(0, data.shape[0] - 1, n_out)
        data = np.stack([np.interp(t_out, t_in, data[:, c]) for c in range(2)], axis=1).astype(np.float32)
    return np.ascontiguousarray(data)


@dataclass
class Voice:
    cue_id: str
    data: np.ndarray
    pos: int
    gain: float
    channel: int            # первый канал выхода (0 = каналы 1-2)
    timeline: bool          # запущен по таймкоду (останавливается вместе с транспортом)
    fade_total: int = 0
    fade_left: int = 0
    done: bool = False


class Mixer:
    def __init__(self):
        self._lock = threading.Lock()
        self.voices: list[Voice] = []

    def start(self, voice: Voice) -> None:
        with self._lock:
            self.voices.append(voice)

    def stop(self, sample_rate: int, fade_s: float = 0.03, only_timeline: bool = False, cue_id: str | None = None) -> None:
        n = max(1, int(fade_s * sample_rate))
        with self._lock:
            for v in self.voices:
                if only_timeline and not v.timeline:
                    continue
                if cue_id is not None and v.cue_id != cue_id:
                    continue
                if v.fade_total == 0:
                    v.fade_total = v.fade_left = n

    def active_cue_ids(self) -> set[str]:
        with self._lock:
            return {v.cue_id for v in self.voices if not v.done}

    def progress(self, cue_id: str) -> float | None:
        with self._lock:
            for v in self.voices:
                if v.cue_id == cue_id and not v.done:
                    return v.pos / max(1, v.data.shape[0])
        return None

    def mix_into(self, out: np.ndarray) -> None:
        frames, nch = out.shape
        with self._lock:
            for v in self.voices:
                if v.done:
                    continue
                remain = v.data.shape[0] - v.pos
                n = min(frames, remain)
                if n <= 0:
                    v.done = True
                    continue
                chunk = v.data[v.pos:v.pos + n] * v.gain
                if v.fade_total:
                    k = min(n, v.fade_left)
                    ramp = np.zeros(n, dtype=np.float32)
                    if k > 0:
                        ramp[:k] = np.linspace(v.fade_left / v.fade_total, (v.fade_left - k) / v.fade_total, k)
                    chunk = chunk * ramp[:, None]
                    v.fade_left -= k
                    if v.fade_left <= 0:
                        v.done = True
                c0 = v.channel
                if c0 < nch:
                    out[:n, c0] += chunk[:, 0]
                if c0 + 1 < nch:
                    out[:n, c0 + 1] += chunk[:, 1]
                v.pos += n
                if v.pos >= v.data.shape[0]:
                    v.done = True
            self.voices = [v for v in self.voices if not v.done]


class AudioOutputs:
    """Открывает потоки вывода. Если звук и LTC идут на одно устройство — один общий поток."""

    def __init__(self, mixer: Mixer, log):
        self.mixer = mixer
        self.log = log
        self.streams: list = []
        self.sample_rate = 48000
        self.ltc_generator = None
        self.error = ""
        self.peak = 0.0
        self.ltc_active = False

    def close(self) -> None:
        for s in self.streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self.streams = []

    def open(self, media_device: str, media_channels: int, sample_rate: int, blocksize: int,
             ltc_generator=None, ltc_device: str = "", ltc_channel: int = 1) -> None:
        self.close()
        self.error = ""
        self.sample_rate = sample_rate
        self.ltc_generator = ltc_generator
        if sd is None:
            self.error = "Звук недоступен: " + AUDIO_IMPORT_ERROR
            return
        media_idx = resolve_device(media_device, "output")
        if media_idx is None:
            try:
                media_idx = sd.default.device[1]
            except Exception:
                media_idx = None
        plan: dict = {}
        plan.setdefault(media_idx, {"media": False, "ltc": None, "channels": 0})
        plan[media_idx]["media"] = True
        plan[media_idx]["channels"] = max(plan[media_idx]["channels"], media_channels)
        if ltc_generator is not None:
            lidx = resolve_device(ltc_device, "output") if ltc_device else media_idx
            if lidx is None:
                lidx = media_idx
            plan.setdefault(lidx, {"media": False, "ltc": None, "channels": 0})
            plan[lidx]["ltc"] = max(0, ltc_channel - 1)
            plan[lidx]["channels"] = max(plan[lidx]["channels"], ltc_channel)
        for idx, p in plan.items():
            try:
                maxch = sd.query_devices(idx)["max_output_channels"] if idx is not None else p["channels"]
                ch = max(1, min(p["channels"], int(maxch)))
                if p["ltc"] is not None and p["ltc"] >= ch:
                    self.log(f"LTC: у устройства только {ch} кан., LTC пойдёт в канал {ch}")
                    p["ltc"] = ch - 1
                if p["ltc"] is not None and p["media"] and p["ltc"] < media_channels:
                    self.log(f"Внимание: LTC идёт в канал {p['ltc'] + 1}, который используется и для звука — "
                             f"звук в этом канале будет заменён таймкодом")
                stream = sd.OutputStream(device=idx, channels=ch, samplerate=sample_rate, blocksize=blocksize,
                                         dtype="float32", latency="low",
                                         callback=self._make_callback(p["media"], p["ltc"]))
                stream.start()
                self.streams.append(stream)
            except Exception as e:
                self.error = f"Не удалось открыть звуковой выход: {e}"
                self.log(self.error)

    def _make_callback(self, media: bool, ltc_ch):
        def cb(outdata, frames, time_info, status):
            outdata.fill(0)
            try:
                if media:
                    self.mixer.mix_into(outdata)
                    if outdata.size:
                        self.peak = max(self.peak * 0.8, float(np.max(np.abs(outdata))))
                if ltc_ch is not None and self.ltc_generator is not None:
                    outdata[:, ltc_ch] = self.ltc_generator.read(frames)
                    self.ltc_active = self.ltc_generator.active
                np.clip(outdata, -1.0, 1.0, out=outdata)
            except Exception:
                outdata.fill(0)
        return cb

    @property
    def output_latency(self) -> float:
        try:
            return float(self.streams[0].latency) if self.streams else 0.0
        except Exception:
            return 0.0


class LTCInput:
    """Вход LTC с канала звуковой карты. on_frame(h, m, s, f, df, t) вызывается из аудиопотока."""

    def __init__(self, decoder_factory, on_frame, log):
        self.decoder_factory = decoder_factory
        self.on_frame = on_frame
        self.log = log
        self.stream = None
        self.decoder = None
        self.error = ""
        self.level = 0.0

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stream = None

    def open(self, device: str, channel: int, sample_rate: int) -> None:
        self.close()
        self.error = ""
        if sd is None:
            self.error = "Звук недоступен: " + AUDIO_IMPORT_ERROR
            return
        idx = resolve_device(device, "input")
        try:
            maxch = int(sd.query_devices(idx if idx is not None else sd.default.device[0])["max_input_channels"])
            ch = max(1, min(channel, maxch))
            self.decoder = self.decoder_factory(sample_rate)
            col = ch - 1

            def cb(indata, frames, time_info, status):
                try:
                    now = time.perf_counter()
                    x = indata[:, col]
                    self.level = max(self.level * 0.8, float(np.max(np.abs(x))) if x.size else 0.0)
                    for h, m, s, f, df, off in self.decoder.feed(x):
                        t = now - (frames - off) / sample_rate
                        self.on_frame(h, m, s, f, df, t)
                except Exception:
                    pass

            self.stream = sd.InputStream(device=idx, channels=ch, samplerate=sample_rate, blocksize=256,
                                         dtype="float32", latency="low", callback=cb)
            self.stream.start()
        except Exception as e:
            self.error = f"Не удалось открыть вход LTC: {e}"
            self.log(self.error)
