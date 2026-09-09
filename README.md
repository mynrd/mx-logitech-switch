# Logitech MX Easy-Switch tools

Two small Python tools for Logitech MX devices connected over **Bluetooth**, built and tested with an MX Master 3 and an MX Keys Mini. Both talk HID++ 2.0 (Logitech's vendor protocol) directly through `hidapi`. No driver, no admin rights, no Logi Options+ needed (it can stay installed).

| Tool | What it does |
|---|---|
| `mx_switch.py` | GUI / CLI to move the mouse to Easy-Switch host 1, 2 or 3 |
| `mx_follow.py` | Background service: when the keyboard (or mouse) switches host, the other device follows |

## Requirements

- Windows, Python 3.12+
- Devices paired over Bluetooth. Unifying/Bolt receivers are not supported.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Always run with the venv interpreter. A system Python may carry a different package also named `hid`, which fails with `Unable to load any of the following libraries ... hidapi.dll`.

## mx_switch: move the mouse

```
.venv\Scripts\python mx_switch.py          # GUI: three buttons, current host highlighted
.venv\Scripts\python mx_switch.py 2        # CLI: switch to host 2
.venv\Scripts\python mx_switch.py status   # CLI: print "host N of 3"
```

`MX Switch.bat` opens the GUI without a console. The GUI polls once per second, shows "Mouse is on another host" while the mouse is away and re-enables the buttons when it comes back.

## mx_follow: keyboard and mouse switch together

Press Host 2 or 3 on the MX Keys Mini and the MX Master 3 jumps to the same host. The reverse direction (mouse button on the bottom moves the keyboard) is implemented with the same code but not yet tested.

```
.venv\Scripts\python mx_follow.py     # foreground, logs to console and mx_follow.log
```

`MX Follow.bat` starts it hidden, `Stop MX Follow.bat` kills it (uses `mx_follow.pid`). To run at login, put a shortcut to `MX Follow.bat` in `shell:startup`.

The service only sees devices while they are connected to this PC, so it can only push devices *away*. To come back to this PC together, run the service on the other machine too, or press both devices' host buttons.

### How it works

The Easy-Switch keys cannot be intercepted (HID++ reports them as not divertable). Instead, right before a device leaves, its firmware sends a Change Host (feature 0x1814) event:

```
11 ff <feature idx> 00 00 <host index>      host index is 0-based
```

`mx_follow.py` keeps one reader thread per device, and on that event writes `setCurrentHost(host index)` to every other connected device. Host numbers are matched 1:1, so pair both devices to the same channel numbers on each machine.

## Troubleshooting

Both tools log to a file next to the script (`mx_switch.log`, `mx_follow.log`).

| Message | Meaning |
|---|---|
| `Mouse is on another host` / `MX Master 3 not found over Bluetooth` | Device not enumerated on this PC's Bluetooth. |
| `HID++ error N` | The device rejected the request. 2 = invalid argument, 7 = invalid host index. |
| `setup failed: read error` right after a reconnect | Windows is still bringing the BLE link up. Retries every 2s on its own. |

## Files

| File | Purpose |
|---|---|
| `mx_switch.py` | Mouse host switcher: HID++ client, CLI, tkinter GUI |
| `mx_follow.py` | Follow service |
| `MX Switch.bat`, `MX Follow.bat`, `Stop MX Follow.bat` | Launchers using the venv's `pythonw` |
| `requirements.txt` | `hidapi` |
