# TOS-13 — Halloween pumpkin light

For the Adafruit Circuit Playground Express with CircuitPython. It uses only the board itself:
the 10 onboard LEDs and the onboard accelerometer. There are no buttons, no sound, and nothing
to attach.

- **Calm:** the LEDs slowly fade up and down: green, then orange, then green, then orange.
- **Vibration:** when a vibration is 4× the 60-second rolling average, the LEDs flash orange
  and green for 3 seconds.
- **Rolling average:** the board keeps a running average of the vibration over the last
  60 seconds and flushes anything older. The trigger therefore adjusts to the room by itself.
  If the table keeps shaking, the board gets used to it. About a minute after it stops, the
  board is fully sensitive again.

Copy `code.py` to the `CIRCUITPY` drive.

Settings at the top of `code.py`:

| Setting | What it changes |
|---|---|
| `SENSITIVITY` | how many times the rolling average a vibration must be (lower = more sensitive) |
| `AVERAGE_TIME` | seconds in the rolling average (60) |
| `FLASH_TIME` | seconds of flashing per vibration (3) |
| `PULSE_TIME` | how slow the calm fade is |
| `FLASH_SPEED` | seconds per colour while flashing |
| `BRIGHTNESS` | overall LED brightness |

`FLASH_SPEED` is 0.17 s, which gives 3 flashes per second. That is the safety limit for
photosensitive epilepsy in WCAG 2.3.1.
