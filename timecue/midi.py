"""MIDI-порты (через mido + python-rtmidi)."""

from __future__ import annotations

try:
    import mido
    mido.set_backend("mido.backends.rtmidi")
    MIDI_IMPORT_ERROR = ""
except Exception as e:
    mido = None
    MIDI_IMPORT_ERROR = str(e)


def midi_available() -> bool:
    if mido is None:
        return False
    try:
        mido.get_output_names()
        return True
    except Exception:
        return False


def list_outputs() -> list[str]:
    try:
        return list(dict.fromkeys(mido.get_output_names())) if mido else []
    except Exception:
        return []


def list_inputs() -> list[str]:
    try:
        return list(dict.fromkeys(mido.get_input_names())) if mido else []
    except Exception:
        return []


class MidiOut:
    def __init__(self, log):
        self.port = None
        self.name = ""
        self.log = log
        self.error = ""

    def open(self, name: str) -> None:
        self.close()
        self.error = ""
        if not name:
            return
        if mido is None:
            self.error = "MIDI недоступен: " + MIDI_IMPORT_ERROR
            return
        try:
            self.port = mido.open_output(name)
            self.name = name
        except Exception as e:
            self.error = f"Не удалось открыть MIDI-выход «{name}»: {e}"
            self.log(self.error)

    def send_bytes(self, data: list[int]) -> bool:
        if self.port is None:
            return False
        try:
            self.port.send(mido.Message.from_bytes(data))
            return True
        except Exception as e:
            msg = f"MIDI: ошибка отправки ({e})"
            if msg != self.error:
                self.error = msg
                self.log(msg)
            return False

    def close(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
        self.name = ""


class MidiIn:
    def __init__(self, on_bytes, log):
        self.port = None
        self.on_bytes = on_bytes
        self.log = log
        self.error = ""

    def open(self, name: str) -> None:
        self.close()
        self.error = ""
        if not name:
            return
        if mido is None:
            self.error = "MIDI недоступен: " + MIDI_IMPORT_ERROR
            return
        try:
            self.port = mido.open_input(name, callback=lambda msg: self.on_bytes(msg.bytes()))
            try:  # rtmidi по умолчанию может отбрасывать MTC и SysEx — включаем их приём
                self.port._rt.ignore_types(False, False, True)
            except Exception:
                pass
        except Exception as e:
            self.error = f"Не удалось открыть MIDI-вход «{name}»: {e}"
            self.log(self.error)

    def close(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
