# TOS-13 — Halloween pumpkin light

For the Adafruit Circuit Playground Express with CircuitPython. It uses only the board itself:
the 10 onboard LEDs and the onboard accelerometer. There are no buttons, no sound, and nothing
to attach.

- **Calm:** the LEDs slowly fade up and down: green, then orange, then green, then orange.
- **Any small vibration:** the LEDs flash orange and green. They keep flashing until the board
  has been still for 2 seconds, then go back to the calm fade.

Copy `code.py` to the `CIRCUITPY` drive.

Settings at the top of `code.py`:

| Setting | What it changes |
|---|---|
| `SENSITIVITY` | how small a vibration triggers the flashing (lower = more sensitive) |
| `PULSE_TIME` | how slow the calm fade is |
| `FLASH_HOLD` | how long it keeps flashing after the vibration stops |
| `FLASH_SPEED` | seconds per colour while flashing |
| `BRIGHTNESS` | overall LED brightness |

`FLASH_SPEED` is 0.17 s, which gives 3 flashes per second. That is the safety limit for
photosensitive epilepsy in WCAG 2.3.1.
