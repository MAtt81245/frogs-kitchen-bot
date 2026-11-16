# Frogs Kitchen Bot

Python bot that plays the idle mobile game *Frogs Kitchen* on a physical Android phone, using ADB and OpenCV. It reads the screen, recognises buttons and items with computer vision, and sends touch inputs back to the device.

Built as a personal project to learn computer vision and device control, developed with AI assistance.

## How it works

1. **Capture:** grabs the phone screen over ADB (raw `screencap`, ~0.5 s per frame) or, much faster (~0.03 s), from a scrcpy video stream exposed through `v4l2loopback`.
2. **Detect:** OpenCV template matching on a half-size copy of the frame (about 5x faster) finds buttons, arrows, boxes and popups. Coins ("tips") spin, so they are detected by gold colour (HSV) and size instead of by template.
3. **Decide:** every target has a type and a priority (tips > boxes > alerts, red arrow before yellow arrow), so the most important element is always handled first.
4. **Act:** touches are sent with `adb shell input swipe`, with small random offsets and durations, batched into one command and sent asynchronously so the next capture starts right away.

## Features

- Priority system and per-target settings (`Objetivo` dataclass): threshold, click offset, which buy buttons it uses, how long to wait for its menu.
- Burst buying: presses the green buy button repeatedly while it is active, then closes the menu.
- Menus without a close button are closed by tapping a neutral spot far from the last button pressed.
- One-time blocking popups (next location, "ready") are handled first.
- Anti-stuck logic: if the same "coin" is tapped several times and never disappears (a false positive), it is ignored for 120 s.
- Automatic ADB recovery: restarts the ADB server and waits for the phone if captures start failing.
- Failure logging: saves annotated screenshots to `fallos/` and prints the similarity score of every buy button, to see what went wrong.
- Timestamped log and a `DEBUG` mode that prints the similarity of every template per frame.

## Requirements

- Python 3.10+, `opencv-python`, `numpy`
- `adb` and an Android phone with USB debugging enabled
- Optional, for fast capture: `scrcpy` and `v4l2loopback` (Linux)

## Setup

1. Connect the phone and check it appears in `adb devices`.
2. Take native screenshots and crop the templates into the same folder as the script. This repository does **not** include game assets. Needed files:
   `caja_new.png`, `caja_rana.png`, `caja_diamante.png`, `flecha_roja.png`, `flecha_amarilla.png`, `flecha_verde.png`, `boton_verde.png`, `boton_verde_2.png`, `cerrar.png`, `boton_siguiente.png`, `proxima_ubicacion.png`, `preparado.png`
3. Review the configuration block at the top of `bot_v2.py`. The resolution, the safe tap points and the coin search area are tuned for a Samsung Galaxy S23 Ultra (1080x2316) and must be adapted for other devices.
4. For fast capture, start scrcpy with `--v4l2-sink=/dev/videoN` and set `CAPTURA_MODO = "v4l2"`.
5. Run: `python bot_v2.py` (Ctrl+C to stop).

## Problems solved along the way

- **Spinning coins:** template matching failed because the sprite changes while rotating; its height and colour stay constant, so detection moved to HSV + size.
- **Menus with no close button:** blind taps sometimes hit the buy button instead; now the bot taps the safe point farthest from the last click.
- **Overnight run stuck in a loop:** the bot kept tapping two decorative elements that looked like coins. This led to the anti-stuck logic, the failure screenshots and the ADB auto-reconnect.
- **Speed:** replacing PNG screenshots with raw capture, then with a video stream, plus downscaled matching, took the loop from about one action per second to near real time.

## Notes

Code comments and log messages are in Spanish. Tested only on a Samsung Galaxy S23 Ultra running from a Linux desktop (CachyOS). Personal and educational project, not affiliated with the game's developers.
