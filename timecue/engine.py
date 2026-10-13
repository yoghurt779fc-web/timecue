"""Движок: транспорт (внутренние часы или следование за MTC/LTC), запуск кью, выходы."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

from . import audio as audio_mod
from . import midi as midi_mod
from .dmx import DmxEngine, DmxSettings
from .ltc import LTCDecoder, LTCGenerator
from .mtc import MTCParser, MTCSequence, full_frame_sysex
from .osc import OscSender, OscTarget, parse_args
from .show import Cue, Show
from .tc import FrameRate, format_tc, frames_to_tc, parse_tc, rate_from_key, rate_from_mtc_code, tc_to_frames

SOURCE_LABELS = {
    "internal": "Внутренние часы",
    "mtc": "Следовать за MTC",
    "ltc": "Следовать за LTC",
}


def _enable_fine_timer() -> None:
    """В Windows по умолчанию sleep() точен лишь до ~15 мс — просим 1 мс."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


def _sleep_until(t: float) -> None:
    while True:
        d = t - time.perf_counter()
        if d <= 0:
            return
        if d > 0.002:
            time.sleep(d - 0.0015)
        else:
            time.sleep(0)


class Engine:
    def __init__(self, show: Show, clock=time.perf_counter, open_devices: bool = True):
        self.show = show
        self.clock = clock
        self.open_devices = open_devices
        self.events: queue.Queue = queue.Queue()
        self._lock = threading.RLock()

        self.fr: FrameRate = rate_from_key(show.settings["timecode"]["fps"])
        self.source = "internal"

        # внутренние часы
        self._int_running = False
        self._int_base = 0.0
        self._int_base_t = 0.0
        self.start_frames = 0

        # следование
        self._chase_frames = 0.0
        self._chase_t = 0.0
        self._chase_last_rx = -1e9
        self._chase_running = False
        self._chase_prev_rx_frames = None
        self._mtc_last_rx = -1e9
        self._ltc_last_rx = -1e9
        self._warned: set[str] = set()

        # планировщик
        self._timeline: list[tuple[int, int, Cue]] = []
        self._last_pos: float | None = None
        self._was_running = False
        self._pending_locate = False
        self.standby: int = 0
        self.last_fired: dict[str, float] = {}

        # выходы
        self.dmx = DmxEngine(log=self.log)
        self.osc = OscSender()
        self.mixer = audio_mod.Mixer()
        self.audio_out = audio_mod.AudioOutputs(self.mixer, self.log)
        self.ltc_in = audio_mod.LTCInput(LTCDecoder, self._on_ltc_frame, self.log)
        self.midi_out = midi_mod.MidiOut(self.log)
        self.midi_in = midi_mod.MidiIn(self._on_midi_bytes, self.log)
        self.mtc_parser = MTCParser()
        self._mtc_seq = MTCSequence(self.fr)
        self._mtc_locate_flag = False
        self._ltc_gen: LTCGenerator | None = None
        self._ltc_next: int | None = None
        self._audio_cache: dict[tuple, object] = {}
        self._loading: set[tuple] = set()
        self.sample_rate = 48000

        self._threads: list[threading.Thread] = []
        self._alive = False

    # ------------------------------------------------------------------ служебное
    def log(self, text: str) -> None:
        self.events.put(("log", time.strftime("%H:%M:%S"), text))

    def _warn_once(self, key: str, text: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.log(text)

    @property
    def settings(self) -> dict:
        return self.show.settings

    # ------------------------------------------------------------------ запуск
    def start(self) -> None:
        _enable_fine_timer()
        self._alive = True
        self.apply_settings()
        self.dmx.start()
        for target, name in ((self._scheduler_loop, "scheduler"), (self._mtc_loop, "mtc-out")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def shutdown(self) -> None:
        self._alive = False
        for t in self._threads:
            t.join(timeout=1)
        self._threads = []
        self.dmx.stop()
        self.audio_out.close()
        self.ltc_in.close()
        self.midi_out.close()
        self.midi_in.close()

    def apply_settings(self) -> None:
        """Применяет настройки шоу: частоту кадров, источник, порты и устройства."""
        st = self.settings
        with self._lock:
            pos = self.position()
            old_fr = self.fr
            self.fr = rate_from_key(st["timecode"]["fps"])
            new_source = st["timecode"].get("source", "internal")
            if new_source not in SOURCE_LABELS:
                new_source = "internal"
            if new_source != self.source:
                self._int_running = False
                self._chase_running = False
            self.source = new_source
            sf = parse_tc(st["timecode"].get("start_tc", "00:00:00:00"), self.fr)
            self.start_frames = sf if sf is not None else 0
            if old_fr.key != self.fr.key:
                # переводим текущую позицию в новую частоту по реальному времени
                pos = pos / old_fr.rate * self.fr.rate
            self._int_base = pos
            self._int_base_t = self.clock()
            self._mtc_seq = MTCSequence(self.fr)
            self._warned.clear()
            self.rebuild_timeline()

        self.dmx.apply_settings(DmxSettings.from_dict(st["dmx"]))
        if not self.open_devices:
            return

        a = st["audio"]
        ltc = st["ltc"]
        self.sample_rate = int(a.get("sample_rate", 48000))
        self._ltc_gen = None
        if ltc.get("out_enabled"):
            self._ltc_gen = LTCGenerator(self.sample_rate, self.fr, self._ltc_frame_source,
                                         float(ltc.get("out_level_db", -10.0)))
        self.audio_out.open(a.get("device", ""), int(a.get("channels", 2)), self.sample_rate,
                            int(a.get("blocksize", 256)), self._ltc_gen,
                            ltc.get("out_device", ""), int(ltc.get("out_channel", 2)))
        if self.source == "ltc":
            self.ltc_in.open(ltc.get("in_device", ""), int(ltc.get("in_channel", 1)), self.sample_rate)
        else:
            self.ltc_in.close()

        m = st["mtc"]
        if m.get("out_enabled") and m.get("out_port"):
            if self.midi_out.name != m["out_port"]:
                self.midi_out.open(m["out_port"])
        else:
            self.midi_out.close()
        if self.source == "mtc" and m.get("in_port"):
            self.midi_in.open(m["in_port"])
        else:
            self.midi_in.close()
        self.preload_audio()

    # ------------------------------------------------------------------ кью
    def rebuild_timeline(self) -> None:
        tl = []
        with self._lock:
            for i, c in enumerate(self.show.cues):
                if not c.enabled or not c.trigger.strip():
                    continue
                fr = parse_tc(c.trigger, self.fr)
                if fr is not None:
                    tl.append((fr, i, c))
            tl.sort(key=lambda x: (x[0], x[1]))
            self._timeline = tl
            if self.standby >= len(self.show.cues):
                self.standby = max(0, len(self.show.cues) - 1)

    def cues_changed(self) -> None:
        self.rebuild_timeline()
        self.preload_audio()

    def cue_frames(self, cue: Cue) -> int | None:
        return parse_tc(cue.trigger, self.fr) if cue.trigger.strip() else None

    def preload_audio(self) -> None:
        if not self.open_devices:
            return
        for c in self.show.cues:
            if c.type == "audio" and c.file:
                self._ensure_loaded(c.file, None)

    def _ensure_loaded(self, path: str, then) -> object | None:
        key = (path, self.sample_rate)
        data = self._audio_cache.get(key)
        if data is not None:
            return data
        if key in self._loading:
            return None
        self._loading.add(key)

        def work():
            try:
                arr = audio_mod.load_audio_file(path, self.sample_rate)
                self._audio_cache[key] = arr
                if then:
                    then(arr)
            except Exception as e:
                self.log(f"Звук: не удалось загрузить «{os.path.basename(path)}»: {e}")
            finally:
                self._loading.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return None

    def audio_duration(self, cue: Cue) -> float | None:
        data = self._audio_cache.get((cue.file, self.sample_rate))
        return None if data is None else data.shape[0] / self.sample_rate

    def fire(self, cue: Cue, source: str = "go", offset_s: float = 0.0, quiet: bool = False) -> None:
        try:
            if cue.type == "audio":
                self._fire_audio(cue, source, offset_s)
            elif cue.type == "dmx":
                if cue.dmx_clear:
                    self.dmx.clear_all()
                vals = []
                for r in cue.dmx_rows:
                    vals.extend(r.expand())
                self.dmx.set_channels(vals)
            elif cue.type == "osc":
                target = self._osc_target(cue.osc_target)
                if target is None:
                    self.log(f"OSC: получатель «{cue.osc_target}» не найден в настройках")
                else:
                    self.osc.send(target.host, target.port, cue.osc_address, parse_args(cue.osc_args))
        except Exception as e:
            self.log(f"Кью {cue.number or ''} «{cue.name}»: ошибка — {e}")
        self.last_fired[cue.id] = time.perf_counter()
        self.events.put(("fired", cue.id))
        if not quiet:
            where = "GO" if source == "go" else format_tc(self.position(), self.fr)
            self.log(f"▶ {cue.number} {cue.name or cue.summary()}  [{where}]")

    def _fire_audio(self, cue: Cue, source: str, offset_s: float) -> None:
        if not cue.file:
            self.log(f"Кью {cue.number}: не выбран звуковой файл")
            return
        gain = 10 ** (cue.volume_db / 20.0)
        t_req = time.perf_counter()

        def start(arr):
            extra = time.perf_counter() - t_req
            pos = int(max(0.0, offset_s + extra) * self.sample_rate)
            if pos >= arr.shape[0]:
                return
            self.mixer.start(audio_mod.Voice(cue.id, arr, pos, gain, max(0, (cue.out_pair - 1) * 2),
                                             timeline=(source == "tc")))

        data = self._ensure_loaded(cue.file, start)
        if data is not None:
            start(data)

    def _osc_target(self, name: str) -> OscTarget | None:
        targets = [OscTarget.from_dict(t) for t in self.settings["osc"].get("targets", [])]
        for t in targets:
            if t.name == name:
                return t
        return targets[0] if (targets and not name) else None

    # ------------------------------------------------------------------ GO / стоп
    def go(self) -> None:
        cues = self.show.cues
        if not cues:
            return
        i = self.standby if 0 <= self.standby < len(cues) else 0
        # пропускаем выключенные
        n = 0
        while not cues[i].enabled and n < len(cues):
            i = (i + 1) % len(cues)
            n += 1
        if not cues[i].enabled:
            return
        self.fire(cues[i], "go")
        self.standby = i + 1 if i + 1 < len(cues) else i
        self.events.put(("standby", self.standby))

    def panic(self) -> None:
        self.mixer.stop(self.sample_rate, 0.25)
        self.log("■ Стоп всего звука")

    def stop_cue(self, cue: Cue) -> None:
        self.mixer.stop(self.sample_rate, 0.1, cue_id=cue.id)

    # ------------------------------------------------------------------ транспорт
    def position(self) -> float:
        now = self.clock()
        if self.source == "internal":
            if self._int_running:
                return self._int_base + (now - self._int_base_t) * self.fr.rate
            return self._int_base
        if self._chase_running and now - self._chase_last_rx <= self._freewheel_s():
            return self._chase_frames + (now - self._chase_t) * self.fr.rate
        return self._chase_frames

    def is_running(self) -> bool:
        if self.source == "internal":
            return self._int_running
        return self._chase_running and (self.clock() - self._chase_last_rx) <= self._freewheel_s()

    def _freewheel_s(self) -> float:
        frames = float(self.settings["timecode"].get("freewheel_frames", 10))
        return max(0.12, frames / self.fr.rate)

    def play(self) -> None:
        if self.source != "internal":
            return
        with self._lock:
            if not self._int_running:
                self._int_base_t = self.clock()
                self._int_running = True

    def pause(self) -> None:
        if self.source != "internal":
            return
        with self._lock:
            if self._int_running:
                self._int_base = self.position()
                self._int_running = False

    def toggle_play(self) -> None:
        if self._int_running:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        """Стоп и возврат к стартовому таймкоду."""
        if self.source != "internal":
            return
        with self._lock:
            self._int_running = False
            self.locate(self.start_frames)

    def locate(self, frames: float) -> None:
        if self.source != "internal":
            return
        with self._lock:
            frames = max(0.0, float(frames))
            self._int_base = frames
            self._int_base_t = self.clock()
            self._mtc_locate_flag = True
            self._ltc_next = None
            if not self._int_running:
                self._handle_stopped_locate(frames)

    def nudge(self, seconds: float) -> None:
        self.locate(max(0.0, self.position() + seconds * self.fr.rate))

    # ------------------------------------------------------------------ планировщик
    def _scheduler_loop(self) -> None:
        nxt = time.perf_counter()
        while self._alive:
            try:
                self.tick()
            except Exception as e:
                self._warn_once("sched:" + str(e), f"Ошибка планировщика: {e}")
            nxt += 0.004
            now = time.perf_counter()
            if nxt < now - 0.1:
                nxt = now
            _sleep_until(nxt)

    def tick(self) -> None:
        with self._lock:
            pos = self.position()
            running = self.is_running()
            rate = self.fr.rate
            if running:
                if not self._was_running:
                    self._on_start(pos)
                else:
                    delta = pos - (self._last_pos if self._last_pos is not None else pos)
                    if self._last_pos is None or delta < -0.3 * rate or delta > 1.0 * rate:
                        self._relocate(pos)
                    elif delta > 0:
                        self._fire_range(self._last_pos, pos)
                        self._last_pos = pos
            else:
                if self._was_running:
                    self._on_stop(pos)
                elif (self.source != "internal" and self._last_pos is not None
                      and abs(pos - self._last_pos) > float(self.settings["timecode"].get("freewheel_frames", 10)) + 2):
                    # внешний источник прыгнул, будучи на паузе (full-frame MTC или «стоящий» LTC)
                    self._handle_stopped_locate(pos)
            self._was_running = running

    def _handle_stopped_locate(self, pos: float) -> None:
        if self.settings["timecode"].get("dmx_tracking", True):
            self._rebuild_dmx(pos)
        self._last_pos = pos - 0.01

    def _on_start(self, pos: float) -> None:
        if self._last_pos is None or abs(pos - self._last_pos) > 1.5:
            if self.settings["timecode"].get("dmx_tracking", True):
                self._rebuild_dmx(pos)
        self._resume_audio(pos)
        self._last_pos = pos - 0.01
        self._fire_range(self._last_pos, pos)
        self._last_pos = pos

    def _on_stop(self, pos: float) -> None:
        self.mixer.stop(self.sample_rate, 0.05, only_timeline=True)
        self._last_pos = pos

    def _relocate(self, pos: float) -> None:
        self.mixer.stop(self.sample_rate, 0.02, only_timeline=True)
        if self.settings["timecode"].get("dmx_tracking", True):
            self._rebuild_dmx(pos)
        self._resume_audio(pos)
        self._last_pos = pos - 0.01
        self._fire_range(self._last_pos, pos)
        self._last_pos = pos
        self.log(f"↷ Переход на {format_tc(pos, self.fr)}")

    def _fire_range(self, a: float, b: float) -> None:
        for frames, _i, cue in self._timeline:
            if frames > b:
                break
            if frames > a:
                self.fire(cue, "tc", offset_s=max(0.0, (b - frames) / self.fr.rate))

    def _resume_audio(self, pos: float) -> None:
        """Запускает звуковые кью, которые к этой позиции уже должны были играть."""
        for frames, _i, cue in self._timeline:
            if frames >= pos - 0.01:
                break
            if cue.type != "audio":
                continue
            dur = self.audio_duration(cue)
            offset = (pos - frames) / self.fr.rate
            if dur is not None and offset < dur:
                self._fire_audio(cue, "tc", offset)
                self.events.put(("fired", cue.id))

    def _rebuild_dmx(self, pos: float) -> None:
        self.dmx.clear_all()
        for frames, _i, cue in self._timeline:
            if frames > pos:
                break
            if cue.type == "dmx":
                if cue.dmx_clear:
                    self.dmx.clear_all()
                vals = []
                for r in cue.dmx_rows:
                    vals.extend(r.expand())
                self.dmx.set_channels(vals)

    # ------------------------------------------------------------------ вход таймкода
    def _chase_update(self, frames: float, t: float, running: bool) -> None:
        with self._lock:
            off = float(self.settings["timecode"].get("chase_offset_frames", 0))
            self._chase_frames = frames + off
            self._chase_t = t
            if running:
                self._chase_last_rx = t
            self._chase_running = running

    def _on_midi_bytes(self, data) -> None:
        if self.source != "mtc":
            return
        res = self.mtc_parser.feed(data)
        if res is None:
            return
        h, m, s, f, rate_code, kind = res
        now = self.clock()
        self._mtc_last_rx = now
        if rate_code != self.fr.mtc_code:
            incoming = rate_from_mtc_code(rate_code)
            self._warn_once("mtc-rate", f"MTC: на входе {incoming.label}, а в настройках {self.fr.label}")
        frames = tc_to_frames(h, m, s, f, self.fr)
        if kind == "qf":
            # последняя четверть приходит через 1.75 кадра после начала передачи
            self._chase_update(frames + 1.75, now, True)
        else:
            self._chase_update(frames, now, False)

    def _on_ltc_frame(self, h, m, s, f, df, t) -> None:
        if self.source != "ltc":
            return
        self._ltc_last_rx = self.clock()
        if bool(df) != self.fr.drop:
            self._warn_once("ltc-df", "LTC: признак drop-frame на входе не совпадает с настройкой частоты")
        frames = tc_to_frames(h, m, s, f, self.fr)
        prev = self._chase_prev_rx_frames
        self._chase_prev_rx_frames = frames
        moving = prev is None or frames != prev
        # слово кадра принято целиком — значит, уже идёт следующий кадр
        self._chase_update(frames + 1, t, moving)

    # ------------------------------------------------------------------ выход таймкода
    def _ltc_frame_source(self):
        if not self.is_running():
            self._ltc_next = None
            return None
        off_ms = float(self.settings["ltc"].get("out_offset_ms", 0.0))
        p = self.position() + off_ms / 1000.0 * self.fr.rate
        if self._ltc_next is None or abs(self._ltc_next - p) > 3:
            self._ltc_next = int(round(p))
        v = self._ltc_next
        self._ltc_next += 1
        return max(0, v)

    def _mtc_loop(self) -> None:
        was_running = False
        nxt = time.perf_counter()
        while self._alive:
            period = 1.0 / (self.fr.rate * 4)
            try:
                if self.midi_out.port is not None:
                    running = self.is_running()
                    pos = self.position()
                    rc = self.fr.mtc_code
                    if running:
                        if not was_running or self._mtc_locate_flag:
                            self._mtc_locate_flag = False
                            self._mtc_seq.restart()
                            self.midi_out.send_bytes(full_frame_sysex(*frames_to_tc(int(pos), self.fr), rc))
                        self.midi_out.send_bytes(self._mtc_seq.next_message(int(pos)))
                    elif was_running or self._mtc_locate_flag:
                        self._mtc_locate_flag = False
                        self.midi_out.send_bytes(full_frame_sysex(*frames_to_tc(int(pos), self.fr), rc))
                    was_running = running
            except Exception as e:
                self._warn_once("mtc-out:" + str(e), f"MTC: ошибка выхода — {e}")
            nxt += period
            now = time.perf_counter()
            if nxt < now - 0.1:
                nxt = now
            _sleep_until(nxt)

    # ------------------------------------------------------------------ статус для интерфейса
    def status(self) -> dict:
        now = self.clock()
        pos = self.position()
        st = self.settings
        dmx_outs = [o for o in st["dmx"].get("outputs", []) if o.get("enabled", True)]
        detected = self.ltc_in.decoder.detected_nominal if self.ltc_in.decoder is not None else None
        return {
            "pos": pos,
            "tc": format_tc(pos, self.fr),
            "running": self.is_running(),
            "source": self.source,
            "fps": self.fr,
            "mtc_in": (now - self._mtc_last_rx) < 0.5,
            "ltc_in": (now - self._ltc_last_rx) < 0.5,
            "ltc_in_level": self.ltc_in.level,
            "ltc_in_fps": detected,
            "mtc_out": self.midi_out.port is not None,
            "mtc_out_name": self.midi_out.name,
            "ltc_out": self._ltc_gen is not None and bool(self.audio_out.streams),
            "ltc_out_active": self.audio_out.ltc_active,
            "dmx_outputs": len(dmx_outs),
            "dmx_ok": self.dmx.last_send_ok,
            "osc_sent": self.osc.sent,
            "audio_error": self.audio_out.error,
            "audio_peak": self.audio_out.peak,
            "playing": self.mixer.active_cue_ids(),
            "standby": self.standby,
            "freewheel": self.source != "internal" and self._chase_running and (now - self._chase_last_rx) > 0.15,
        }
