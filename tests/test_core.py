"""Проверки ядра без звука, MIDI и интерфейса. Запуск: python -m pytest tests  (или python tests/test_core.py)"""

import os
import socket
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timecue.dmx import DmxEngine, DmxOutput, DmxSettings, artnet_packet, parse_channel_spec, sacn_packet  # noqa: E402
from timecue.engine import Engine  # noqa: E402
from timecue.ltc import LTCDecoder, LTCGenerator  # noqa: E402
from timecue.mtc import MTCParser, MTCSequence, full_frame_sysex  # noqa: E402
from timecue.osc import encode_message, parse_args  # noqa: E402
from timecue.show import Cue, DmxRow, Show  # noqa: E402
from timecue.tc import FRAME_RATES, RATE_BY_KEY, format_tc, frames_per_day, frames_to_tc, parse_tc, tc_to_frames  # noqa: E402


def test_tc_roundtrip():
    for fr in FRAME_RATES:
        for n in list(range(0, 4000)) + list(range(frames_per_day(fr) - 2000, frames_per_day(fr))):
            assert tc_to_frames(*frames_to_tc(n, fr), fr) == n
    df = RATE_BY_KEY["29.97df"]
    assert format_tc(1800, df) == "00:01:00;02"
    assert parse_tc("01:00:00:00", RATE_BY_KEY["25"]) == 90000
    assert parse_tc("1:30", RATE_BY_KEY["25"]) == 2250
    assert parse_tc("90", RATE_BY_KEY["25"]) == 2250
    assert parse_tc("00:00:00:25", RATE_BY_KEY["25"]) is None
    assert parse_tc("abc", RATE_BY_KEY["25"]) is None


def _ltc_roundtrip(sr, fr, invert=False, gain=0.5, noise=0.01):
    start = tc_to_frames(23, 59, 57, 0, fr)
    cnt = [start]

    def src():
        v = cnt[0]
        cnt[0] += 1
        return v

    g = LTCGenerator(sr, fr, src, -6)
    sig = np.concatenate([g.read(441) for _ in range(int(sr * 4 / 441))])
    sig = sig * gain * (-1 if invert else 1) + np.random.default_rng(1).normal(0, noise, sig.size).astype(np.float32)
    d = LTCDecoder(sr)
    out = []
    for i in range(0, sig.size, 256):
        out += d.feed(sig[i:i + 256])
    nums = [tc_to_frames(h, m, s, f, fr) for h, m, s, f, df, o in out]
    exp = [(nums[0] + k) % frames_per_day(fr) for k in range(len(nums))]
    return nums, exp, out, d


def test_ltc_all_rates():
    for sr in (44100, 48000, 96000):
        for fr in FRAME_RATES:
            nums, exp, out, d = _ltc_roundtrip(sr, fr)
            assert nums == exp, (sr, fr.key)
            assert len(nums) >= int(fr.rate * 4) - 3
            assert all(o[4] == fr.drop for o in out)
            assert d.detected_nominal == fr.nominal


def test_ltc_inverted_and_quiet():
    fr = RATE_BY_KEY["25"]
    nums, exp, _, _ = _ltc_roundtrip(48000, fr, invert=True, gain=0.05, noise=0.002)
    assert nums == exp and len(nums) > 90


def test_mtc_roundtrip():
    fr = RATE_BY_KEY["25"]
    seq, p = MTCSequence(fr), MTCParser()
    got = []
    for q in range(400):
        frame = 5000 + q // 4
        r = p.feed(seq.next_message(frame))
        if r:
            got.append((tc_to_frames(*r[:4], fr), r[4]))
    assert got[0] == (5000, 1) and got[1][0] == 5002 and len(got) == 50
    r = p.feed(full_frame_sysex(1, 2, 3, 4, 1))
    assert r == (1, 2, 3, 4, 1, "full")


def test_packets():
    pkt = artnet_packet(0x123, bytes(range(256)) * 2, sequence=7)
    assert pkt[:8] == b"Art-Net\x00" and pkt[8:10] == b"\x00\x50" and pkt[14] == 0x23 and pkt[15] == 0x01
    assert len(pkt) == 18 + 512 and pkt[16:18] == b"\x02\x00"
    pkt = sacn_packet(7, bytes([1, 2, 3]), b"\x11" * 16, "Test", 100, 9)
    assert len(pkt) == 638
    assert struct.unpack(">H", pkt[16:18])[0] & 0x0FFF == 622
    assert struct.unpack(">H", pkt[38:40])[0] & 0x0FFF == 600
    assert struct.unpack(">H", pkt[113:115])[0] == 7
    assert struct.unpack(">H", pkt[115:117])[0] & 0x0FFF == 523
    assert pkt[125] == 0 and pkt[126:129] == bytes([1, 2, 3])
    assert parse_channel_spec("1-3, 10,600") == [1, 2, 3, 10]


def test_osc():
    args = parse_args('1 2.5 "hello world" true i:3.7 s:42')
    assert args == [1, 2.5, "hello world", True, 3, "42"]
    msg = encode_message("/cue/go", [1, 2.5, "hi"])
    assert msg[:8] == b"/cue/go\x00" and msg[8:12] == b",ifs" and len(msg) % 4 == 0
    try:
        from pythonosc.osc_message import OscMessage
        m = OscMessage(msg)
        assert m.address == "/cue/go" and m.params == [1, 2.5, "hi"]
    except ImportError:
        pass


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _udp_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.5)
    return s


def test_engine_timeline():
    lis = _udp_listener()
    show = Show()
    show.settings["osc"]["targets"] = [{"name": "Тест", "host": "127.0.0.1", "port": lis.getsockname()[1]}]
    show.cues = [
        Cue(type="dmx", number="1", trigger="00:00:01:00", dmx_rows=[DmxRow(1, "1-3", 255)]),
        Cue(type="osc", number="2", trigger="00:00:02:00", osc_target="Тест", osc_address="/hello", osc_args="5"),
        Cue(type="dmx", number="3", trigger="00:00:03:00", dmx_rows=[DmxRow(1, "2", 10)], dmx_clear=True),
        Cue(type="osc", number="4", trigger="", osc_target="Тест", osc_address="/manual"),
    ]
    clk = FakeClock()
    e = Engine(show, clock=clk, open_devices=False)
    e.apply_settings()
    e.play()
    for _ in range(int(1.5 / 0.004)):
        clk.t += 0.004
        e.tick()
    assert e.dmx.snapshot(1)[:3] == bytes([255, 255, 255])
    for _ in range(int(1.0 / 0.004)):
        clk.t += 0.004
        e.tick()
    data, _ = lis.recvfrom(1024)
    assert data.startswith(b"/hello\x00")
    for _ in range(int(1.0 / 0.004)):
        clk.t += 0.004
        e.tick()
    assert e.dmx.snapshot(1)[:3] == bytes([0, 10, 0])
    # перемотка назад — DMX восстанавливается по кью до этой точки
    e.locate(tc_to_frames(0, 0, 1, 10, e.fr))
    clk.t += 0.004
    e.tick()
    assert e.dmx.snapshot(1)[:3] == bytes([255, 255, 255])
    # пауза, GO
    e.pause()
    e.standby = 3
    e.go()
    data, _ = lis.recvfrom(1024)
    assert data.startswith(b"/manual\x00")
    # стоп возвращает в начало и гасит DMX
    e.stop()
    assert e.position() == 0 and e.dmx.snapshot(1)[:3] == bytes(3)
    lis.close()


def test_engine_chase_ltc():
    show = Show()
    show.settings["timecode"]["source"] = "ltc"
    show.cues = [Cue(type="dmx", number="1", trigger="10:00:00:10", dmx_rows=[DmxRow(1, "5", 77)])]
    clk = FakeClock()
    e = Engine(show, clock=clk, open_devices=False)
    e.apply_settings()
    fr = e.fr
    # приходят кадры LTC начиная с 10:00:00:00
    for k in range(20):
        h, m, s, f = frames_to_tc(tc_to_frames(10, 0, 0, 0, fr) + k, fr)
        clk.t += 1 / fr.rate
        e._on_ltc_frame(h, m, s, f, False, clk.t)
        e.tick()
    assert e.is_running()
    assert e.dmx.snapshot(1)[4] == 77
    # сигнал пропал — после freewheel транспорт останавливается
    clk.t += 1.0
    e.tick()
    assert not e.is_running()


def test_dmx_sender_loopback():
    lis = _udp_listener()
    eng = DmxEngine()
    eng.apply_settings(DmxSettings(outputs=[DmxOutput(True, 1, "artnet", 3, "127.0.0.1", "")]))
    eng.set_channels([(1, 1, 200), (1, 512, 9)])
    pkts = eng.build_packets()
    assert len(pkts) == 1 and pkts[0][2] == ("127.0.0.1", 6454)
    lis.close()


def test_show_save_load(tmp_path=None):
    import tempfile
    d = tmp_path or tempfile.mkdtemp()
    show = Show()
    show.cues = [Cue(type="audio", number="1", name="Интро", file=os.path.join(str(d), "a.wav"), trigger="00:00:05:00"),
                 Cue(type="dmx", number="2", dmx_rows=[DmxRow(2, "1-4", 128)])]
    p = os.path.join(str(d), "show.tcshow")
    show.save(p)
    s2 = Show.load(p)
    assert [c.to_dict() for c in s2.cues] == [c.to_dict() for c in show.cues]


def test_audio_callback_mix_and_ltc():
    from timecue import audio as A
    fr = RATE_BY_KEY["25"]
    mixer = A.Mixer()
    outs = A.AudioOutputs(mixer, lambda m: None)
    cnt = [1000]

    def src():
        cnt[0] += 1
        return cnt[0]

    outs.ltc_generator = LTCGenerator(48000, fr, src, -10)
    cb = outs._make_callback(True, 2)
    tone = (np.ones((48000, 2)) * 0.25).astype(np.float32)
    mixer.start(A.Voice("c1", tone, 0, 1.0, 0, timeline=True))
    blocks = []
    for k in range(200):
        buf = np.zeros((256, 3), dtype=np.float32)
        cb(buf, 256, None, None)
        blocks.append(buf.copy())
        if k == 100:
            mixer.stop(48000, 0.01, only_timeline=True)
    sig = np.concatenate(blocks)
    assert abs(sig[1000, 0] - 0.25) < 1e-6 and abs(sig[1000, 1] - 0.25) < 1e-6
    assert np.all(sig[150 * 256:, :2] == 0)  # остановлено с фейдом
    assert "c1" not in mixer.active_cue_ids()
    d = LTCDecoder(48000)
    got = d.feed(sig[:, 2])
    nums = [tc_to_frames(*g[:4], fr) for g in got]
    assert len(nums) > 20 and nums == list(range(nums[0], nums[0] + len(nums)))


def test_engine_threads_realtime():
    show = Show()
    show.cues = [Cue(type="dmx", number="1", trigger="00:00:00:05", dmx_rows=[DmxRow(1, "1", 99)])]
    e = Engine(show, open_devices=False)
    e.start()
    try:
        e.play()
        import time as _t
        _t.sleep(0.5)
        assert e.dmx.snapshot(1)[0] == 99
        assert 0.4 * 25 < e.position() < 0.7 * 25
    finally:
        e.shutdown()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
