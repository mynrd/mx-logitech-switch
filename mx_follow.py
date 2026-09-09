"""Background service: when one Logitech device switches Easy-Switch host, send the other along.

The keyboard (and mouse) emit a HID++ Change Host (0x1814) event right before leaving:
    11 ff <idx> 00 00 <hostIndex>
hostIndex is 0-based. On that event every other connected device is told to switch to the same host.

Runs with a system tray icon (bottom right). Right-click it for device status, the log, and Exit.
Starting it while another instance is running stops the old one first.

Usage:
    python mx_follow.py            # logs to console + mx_follow.log
    MX Follow.bat / Stop MX Follow.bat
"""
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import hid
import pystray
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "mx_follow.log")
PID_PATH = os.path.join(HERE, "mx_follow.pid")
VID = 0x046D
HIDPP_BLE_USAGE_PAGE = 0xFF43
LONG_REPORT = 0x11
DEV_INDEX = 0xFF
FEATURE_CHANGE_HOST = 0x1814
SW_ID = 0x0A
REPLY_TIMEOUT_S = 3        # a stale BLE handle right after reconnect never answers; give up fast and reopen
PENDING_WINDOW_S = 20      # forward a switch to a device that connects this soon after the event

DEVICES = {                     # name: substring of the BLE product string
    "keyboard": "mx_keys_mini",
    "mouse": "mx_master_3",
}

log = logging.getLogger("mx_follow")


class Device(threading.Thread):
    def __init__(self, name, product_match, on_switch, on_connect):
        super().__init__(name=name, daemon=True)
        self.name = name
        self.match = product_match
        self.on_switch = on_switch
        self.on_connect = on_connect
        self.h = None
        self.change_host_idx = None
        self.host_count = None
        self.current_host = None    # 1-based, as seen from this PC
        self.lock = threading.Lock()

    # ---- discovery / setup -------------------------------------------------
    def _find_path(self):
        for d in hid.enumerate(VID):
            if d["usage_page"] == HIDPP_BLE_USAGE_PAGE and self.match in (d["product_string"] or "").lower():
                return d["path"]
        return None

    def _call(self, feat_idx, func, params=b""):
        header = (func << 4) | SW_ID
        with self.lock:
            self.h.write((bytes([LONG_REPORT, DEV_INDEX, feat_idx, header]) + params).ljust(20, b"\0"))
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            r = bytes(self.h.read(64, timeout_ms=500))
            if not r:
                continue
            if r[0] != LONG_REPORT:
                continue
            if r[2] == 0xFF and r[3] == feat_idx and r[4] == header:
                raise RuntimeError(f"{self.name}: HID++ error {r[5]}")
            if r[2] == feat_idx and r[3] == header:
                return r[4:]
            self._handle_packet(r)     # events can arrive while we wait for a reply
        raise TimeoutError(f"{self.name}: reply to feature {feat_idx} func {func} never came")

    def _setup(self):
        r = self._call(0x0000, 0, FEATURE_CHANGE_HOST.to_bytes(2, "big"))
        self.change_host_idx = r[0]
        if not self.change_host_idx:
            raise RuntimeError(f"{self.name}: no Change Host feature")
        r = self._call(self.change_host_idx, 0)
        self.host_count, self.current_host = r[0], r[1] + 1

    # ---- actions -----------------------------------------------------------
    @property
    def connected(self):
        return self.h is not None and self.change_host_idx is not None

    def switch_to(self, host):
        """Fire and forget: the device drops the link right after, so no reply is read."""
        pkt = bytes([LONG_REPORT, DEV_INDEX, self.change_host_idx, (1 << 4) | SW_ID, host - 1]).ljust(20, b"\0")
        with self.lock:
            self.h.write(pkt)
        log.info("%s: sent switch to host %d", self.name, host)

    def _handle_packet(self, r):
        # Change Host event: function nibble 0, software id 0 (firmware-originated), params [?, hostIndex]
        if r[2] == self.change_host_idx and r[3] == 0x00:
            target = r[5] + 1
            log.info("%s: Change Host event -> host %d  (%s)", self.name, target, r[:6].hex(" "))
            self.on_switch(self, target)

    # ---- main loop ---------------------------------------------------------
    def run(self):
        while True:
            path = self._find_path()
            if not path:
                time.sleep(1)
                continue
            self.h = hid.device()
            try:
                self.h.open_path(path)
                self._setup()
                log.info("%s: connected, host %d of %d", self.name, self.current_host, self.host_count)
                self.on_connect(self)
                while True:
                    r = bytes(self.h.read(64, timeout_ms=500))
                    if r and r[0] == LONG_REPORT:
                        self._handle_packet(r)
            except Exception as e:
                if self.change_host_idx is None:
                    log.warning("%s: setup failed: %s", self.name, e)
                    time.sleep(2)
                else:
                    log.info("%s: disconnected (%s)", self.name, e)
            finally:
                try:
                    self.h.close()
                except Exception:
                    pass
                self.h = None
                self.change_host_idx = None
                time.sleep(1)


def stop_previous_instance():
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return
    if pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log.info("stopped previous instance (pid %d)", pid)
        time.sleep(0.5)
    except OSError:
        pass


def make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(40, 40, 40, 255))
    d.rounded_rectangle((16, 10, 48, 54), radius=12, outline=(240, 240, 240, 255), width=4)   # mouse body
    d.line((32, 10, 32, 28), fill=(240, 240, 240, 255), width=4)                                # button split
    return img


def build_tray(devices):
    def status_line(d):
        return lambda item: f"{d.name.capitalize()}: host {d.current_host} of {d.host_count}" if d.connected             else f"{d.name.capitalize()}: away"

    def open_log(icon, item):
        subprocess.Popen(["notepad.exe", LOG_PATH])

    def quit_app(icon, item):
        log.info("exit from tray")
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("MX Follow", None, enabled=False),
        pystray.Menu.SEPARATOR,
        *[pystray.MenuItem(status_line(d), None, enabled=False) for d in devices],
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open log", open_log),
        pystray.MenuItem("Exit", quit_app),
    )
    return pystray.Icon("mx_follow", make_icon_image(), "MX Follow", menu)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
    )
    stop_previous_instance()
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))

    devices = []
    pending = {"source": None, "target": None, "at": 0.0}

    def send(d, target):
        if d.current_host == target:
            log.info("%s: already on host %d, nothing to do", d.name, target)
            return
        try:
            d.switch_to(target)
        except Exception as e:
            log.error("%s: switch failed: %s", d.name, e)

    def on_switch(source, target):
        pending.update(source=source, target=target, at=time.monotonic())
        for d in devices:
            if d is source:
                continue
            if not d.connected:
                log.info("%s: not connected yet, will forward host %d if it appears within %ds",
                         d.name, target, PENDING_WINDOW_S)
                continue
            send(d, target)

    def on_connect(d):
        if pending["target"] is None:
            return
        if d is pending["source"]:
            log.info("%s: back before the others followed, dropping pending host %d", d.name, pending["target"])
            pending["target"] = None
            return
        age = time.monotonic() - pending["at"]
        if age <= PENDING_WINDOW_S:
            log.info("%s: connected %.1fs after event, forwarding to host %d", d.name, age, pending["target"])
            send(d, pending["target"])
        else:
            pending["target"] = None

    for name, match in DEVICES.items():
        devices.append(Device(name, match, on_switch, on_connect))
    for d in devices:
        d.start()
    log.info("mx_follow started, watching %s", ", ".join(DEVICES))
    try:
        build_tray(devices).run()      # blocks on the tray message loop until Exit
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
