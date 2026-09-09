"""MX Master 3 Easy-Switch host switcher (Bluetooth).

Usage:
    python mx_switch.py          # GUI
    python mx_switch.py 2        # switch to host 2 and exit
    python mx_switch.py status   # print current host
"""
import logging
import os
import sys
import hid

log = logging.getLogger("mx_switch")

VID = 0x046D
PRODUCT_MATCH = "mx_master_3"
HIDPP_BLE_USAGE_PAGE = 0xFF43   # HID++ collection when connected over Bluetooth LE
LONG_REPORT = 0x11
DEVICE_INDEX_BLE = 0xFF
FEATURE_ROOT = 0x0000
FEATURE_CHANGE_HOST = 0x1814
SW_ID = 0x0A


class MouseNotFound(Exception):
    pass


class HidppError(Exception):
    pass


def _find_path():
    for d in hid.enumerate(VID):
        if d["usage_page"] == HIDPP_BLE_USAGE_PAGE and PRODUCT_MATCH in (d["product_string"] or "").lower():
            return d["path"]
    raise MouseNotFound("MX Master 3 not found over Bluetooth")


class MxMaster3:
    def __init__(self):
        self._h = hid.device()
        self._h.open_path(_find_path())
        self._change_host_idx = self._call(FEATURE_ROOT, 0, bytes([FEATURE_CHANGE_HOST >> 8, FEATURE_CHANGE_HOST & 0xFF]))[0]
        if self._change_host_idx == 0:
            raise HidppError("Device does not support Change Host (0x1814)")

    def close(self):
        self._h.close()

    def _call(self, feature_idx, func, params=b"", timeout_ms=1000, retries=10):
        header = (func << 4) | SW_ID
        pkt = bytes([LONG_REPORT, DEVICE_INDEX_BLE, feature_idx, header]) + params
        self._h.write(pkt.ljust(20, b"\0"))
        for _ in range(retries):
            r = bytes(self._h.read(64, timeout_ms=timeout_ms))
            if not r:
                return None
            if r[0] != LONG_REPORT:
                continue
            if r[2] == 0xFF and r[3] == feature_idx and r[4] == header:
                raise HidppError(f"HID++ error code {r[5]}")
            if r[2] == feature_idx and r[3] == header:
                return r[4:]
        return None

    def host_info(self):
        """Returns (host_count, current_host) with current_host 1-based."""
        r = self._call(self._change_host_idx, 0)
        if r is None:
            raise HidppError("No reply to getHostInfo")
        return r[0], r[1] + 1

    def switch_to(self, host):
        """host is 1-based. The mouse drops this connection immediately, so no reply is expected."""
        self._call(self._change_host_idx, 1, bytes([host - 1]), timeout_ms=300, retries=1)


def cli(arg):
    try:
        m = MxMaster3()
    except MouseNotFound as e:
        print(e)
        return 1
    try:
        count, cur = m.host_info()
        if arg == "status":
            print(f"host {cur} of {count}")
            return 0
        target = int(arg)
        if not 1 <= target <= count:
            print(f"host must be 1..{count}")
            return 1
        if target == cur:
            print(f"already on host {cur}")
            return 0
        m.switch_to(target)
        print(f"switched {cur} -> {target}")
        return 0
    finally:
        m.close()


def gui():
    import tkinter as tk
    from tkinter import ttk

    logging.basicConfig(filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mx_switch.log"),
                        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("GUI started")

    root = tk.Tk()
    root.title("MX Master 3")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    status = tk.StringVar(value="Looking for mouse...")
    frame = ttk.Frame(root, padding=12)
    frame.grid()
    buttons = []
    for i in range(3):
        b = ttk.Button(frame, text=str(i + 1), width=6)
        b.grid(row=0, column=i, padx=4, ipady=12)
        buttons.append(b)
    ttk.Label(frame, textvariable=status, anchor="center").grid(row=1, column=0, columnspan=3, pady=(10, 0))

    style = ttk.Style(root)
    style.configure("Current.TButton", font=("Segoe UI", 12, "bold"))
    style.configure("TButton", font=("Segoe UI", 12))

    last = {"state": None}

    def refresh():
        try:
            m = MxMaster3()
            try:
                count, cur = m.host_info()
            finally:
                m.close()
            if last["state"] != cur:
                log.info("mouse connected on host %s of %s", cur, count)
                last["state"] = cur
            status.set(f"Connected on host {cur}")
            for i, b in enumerate(buttons):
                b.configure(style="Current.TButton" if i + 1 == cur else "TButton",
                            state="disabled" if (i + 1 == cur or i >= count) else "normal")
        except MouseNotFound:
            if last["state"] != "away":
                log.info("mouse left this host")
                last["state"] = "away"
            status.set("Mouse is on another host")
            for b in buttons:
                b.configure(style="TButton", state="disabled")
        except Exception as e:
            log.exception("refresh failed")
            status.set(f"{type(e).__name__}: {e}")
        finally:
            root.after(1000, refresh)

    def switch(host):
        try:
            m = MxMaster3()
            try:
                m.switch_to(host)
            finally:
                m.close()
            status.set(f"Switched to host {host}")
        except Exception as e:
            log.exception("switch failed")
            status.set(f"{type(e).__name__}: {e}")

    for i, b in enumerate(buttons):
        b.configure(command=lambda h=i + 1: switch(h))

    root.after(100, refresh)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1]))
    gui()
