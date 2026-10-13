"""DMX по сети: Art-Net и sACN (E1.31). Хранит значения вселенных и рассылает их."""

from __future__ import annotations

import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field

ARTNET_PORT = 6454
SACN_PORT = 5568


def artnet_packet(universe: int, data: bytes, sequence: int = 0, physical: int = 0) -> bytes:
    """ArtDmx. universe — 15-битный адрес порта (Net:SubNet:Universe), с нуля."""
    data = bytes(data[:512])
    if len(data) % 2:
        data += b"\x00"
    if len(data) < 2:
        data = data + b"\x00" * (2 - len(data))
    return (
        b"Art-Net\x00"
        + struct.pack("<H", 0x5000)
        + struct.pack(">H", 14)
        + bytes([sequence & 0xFF, physical & 0xFF, universe & 0xFF, (universe >> 8) & 0x7F])
        + struct.pack(">H", len(data))
        + data
    )


def sacn_packet(universe: int, data: bytes, cid: bytes, source_name: str = "TimeCue",
                priority: int = 100, sequence: int = 0) -> bytes:
    """E1.31 Data Packet. universe — 1..63999."""
    data = bytes(data[:512]).ljust(512, b"\x00")
    name = source_name.encode("utf-8")[:63].ljust(64, b"\x00")
    prop_count = 1 + len(data)
    dmp = (
        struct.pack(">H", 0x7000 | (10 + prop_count))
        + b"\x02\xa1"
        + struct.pack(">HHH", 0, 1, prop_count)
        + b"\x00" + data
    )
    framing = (
        struct.pack(">H", 0x7000 | (77 + len(dmp)))
        + struct.pack(">I", 0x00000002)
        + name
        + bytes([priority & 0xFF])
        + struct.pack(">H", 0)
        + bytes([sequence & 0xFF, 0])
        + struct.pack(">H", universe)
        + dmp
    )
    root = (
        struct.pack(">HH", 0x0010, 0x0000)
        + b"ASC-E1.17\x00\x00\x00"
        + struct.pack(">H", 0x7000 | (22 + len(framing)))
        + struct.pack(">I", 0x00000004)
        + cid
        + framing
    )
    return root


def sacn_multicast_ip(universe: int) -> str:
    return f"239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}"


def local_ipv4_addresses() -> list[str]:
    """Список IPv4-адресов этого компьютера (для выбора сетевой карты)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    for probe in ("10.255.255.255", "192.168.255.255", "2.255.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 1))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    ips.discard("0.0.0.0")
    return sorted(ips, key=lambda a: (a.startswith("127."), a))


@dataclass
class DmxOutput:
    """Одна строка в настройках: какую «вселенную кью» куда отправлять."""
    enabled: bool = True
    universe: int = 1            # номер вселенной, как он указан в кью (1..)
    protocol: str = "artnet"     # 'artnet' | 'sacn'
    net_universe: int = 0        # номер в сети: Art-Net с 0, sACN с 1
    dest_ip: str = ""            # пусто: Art-Net — широковещательно, sACN — мультикаст
    local_ip: str = ""           # сетевая карта; пусто — автоматически

    def to_dict(self) -> dict:
        return dict(enabled=self.enabled, universe=self.universe, protocol=self.protocol,
                    net_universe=self.net_universe, dest_ip=self.dest_ip, local_ip=self.local_ip)

    @staticmethod
    def from_dict(d: dict) -> "DmxOutput":
        o = DmxOutput()
        for k in o.to_dict():
            if k in d:
                setattr(o, k, d[k])
        o.universe = int(o.universe)
        o.net_universe = int(o.net_universe)
        o.protocol = "sacn" if str(o.protocol).lower() == "sacn" else "artnet"
        return o


@dataclass
class DmxSettings:
    outputs: list[DmxOutput] = field(default_factory=lambda: [DmxOutput()])
    refresh_hz: float = 30.0
    sacn_priority: int = 100
    source_name: str = "TimeCue"
    artnet_broadcast: str = "255.255.255.255"

    def to_dict(self) -> dict:
        return dict(outputs=[o.to_dict() for o in self.outputs], refresh_hz=self.refresh_hz,
                    sacn_priority=self.sacn_priority, source_name=self.source_name,
                    artnet_broadcast=self.artnet_broadcast)

    @staticmethod
    def from_dict(d: dict) -> "DmxSettings":
        s = DmxSettings()
        if "outputs" in d:
            s.outputs = [DmxOutput.from_dict(x) for x in d["outputs"]]
        s.refresh_hz = float(d.get("refresh_hz", s.refresh_hz))
        s.sacn_priority = int(d.get("sacn_priority", s.sacn_priority))
        s.source_name = str(d.get("source_name", s.source_name))
        s.artnet_broadcast = str(d.get("artnet_broadcast", s.artnet_broadcast))
        return s


class DmxEngine:
    """Держит значения всех вселенных и в отдельном потоке рассылает их с заданной частотой."""

    def __init__(self, log=None):
        self._lock = threading.Lock()
        self._universes: dict[int, bytearray] = {}
        self._settings = DmxSettings()
        self._sockets: dict[str, socket.socket] = {}
        self._seq: dict[tuple, int] = {}
        self._cid = uuid.uuid4().bytes
        self._thread: threading.Thread | None = None
        self._running = False
        self._log = log or (lambda msg: None)
        self.last_send_ok = False
        self.last_error = ""
        self.packets_sent = 0

    # ---------- значения ----------
    def universe(self, u: int) -> bytearray:
        buf = self._universes.get(u)
        if buf is None:
            buf = bytearray(512)
            self._universes[u] = buf
        return buf

    def set_channels(self, values: list[tuple[int, int, int]]) -> None:
        """values: [(вселенная, канал 1..512, значение 0..255)]"""
        with self._lock:
            for u, ch, v in values:
                if 1 <= ch <= 512:
                    self.universe(u)[ch - 1] = max(0, min(255, int(v)))

    def clear_all(self) -> None:
        with self._lock:
            for buf in self._universes.values():
                buf[:] = bytes(512)

    def snapshot(self, u: int) -> bytes:
        with self._lock:
            return bytes(self.universe(u))

    # ---------- настройки и поток ----------
    def apply_settings(self, settings: DmxSettings) -> None:
        with self._lock:
            self._settings = settings
            for s in self._sockets.values():
                try:
                    s.close()
                except OSError:
                    pass
            self._sockets.clear()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="dmx-out", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def _socket_for(self, local_ip: str) -> socket.socket:
        s = self._sockets.get(local_ip)
        if s is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
            if local_ip:
                s.bind((local_ip, 0))
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
                except OSError:
                    pass
            self._sockets[local_ip] = s
        return s

    def build_packets(self) -> list[tuple[str, bytes, tuple[str, int]]]:
        """Готовит пакеты для всех включённых выходов: [(local_ip, пакет, (ip, порт))]."""
        out = []
        with self._lock:
            st = self._settings
            for o in st.outputs:
                if not o.enabled:
                    continue
                data = bytes(self.universe(o.universe))
                key = (o.protocol, o.net_universe, o.dest_ip, o.local_ip)
                seq = (self._seq.get(key, 0) % 255) + 1
                self._seq[key] = seq
                if o.protocol == "sacn":
                    nu = max(1, min(63999, o.net_universe))
                    pkt = sacn_packet(nu, data, self._cid, st.source_name, st.sacn_priority, seq)
                    dest = (o.dest_ip.strip() or sacn_multicast_ip(nu), SACN_PORT)
                else:
                    pkt = artnet_packet(max(0, min(32767, o.net_universe)), data, seq)
                    dest = (o.dest_ip.strip() or st.artnet_broadcast, ARTNET_PORT)
                out.append((o.local_ip.strip(), pkt, dest))
        return out

    def send_once(self) -> None:
        ok = True
        for local_ip, pkt, dest in self.build_packets():
            try:
                self._socket_for(local_ip).sendto(pkt, dest)
                self.packets_sent += 1
            except OSError as e:
                ok = False
                msg = f"DMX: не удалось отправить на {dest[0]} ({e})"
                if msg != self.last_error:
                    self.last_error = msg
                    self._log(msg)
                self._sockets.pop(local_ip, None)
        self.last_send_ok = ok

    def _run(self) -> None:
        nxt = time.perf_counter()
        while self._running:
            period = 1.0 / max(1.0, min(44.0, self._settings.refresh_hz))
            try:
                self.send_once()
            except Exception as e:  # поток не должен падать
                self._log(f"DMX: ошибка {e}")
            nxt += period
            delay = nxt - time.perf_counter()
            if delay < -0.5:
                nxt = time.perf_counter()
            elif delay > 0:
                time.sleep(delay)


def parse_channel_spec(text: str) -> list[int]:
    """'1', '1-10', '1,3,5-8' -> список каналов 1..512."""
    chans: list[int] = []
    for part in str(text).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if a > b:
                a, b = b, a
            chans.extend(range(a, b + 1))
        else:
            chans.append(int(part))
    return [c for c in chans if 1 <= c <= 512]
