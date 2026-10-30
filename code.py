# Halloween pumpkin light for the Adafruit Circuit Playground Express
# (CircuitPython). Uses only the board itself: the 10 onboard LEDs and
# the onboard accelerometer. No buttons, no sound, nothing attached.
#
# Calm: the LEDs slowly fade up and down -- green, then orange, then
# green, then orange.
# Any small vibration: the LEDs flash orange and green, and keep flashing
# until the board has been still for FLASH_HOLD seconds.

import math
import time

from adafruit_circuitplayground import cp

BRIGHTNESS = 0.3     # LED brightness (0..1)
PULSE_TIME = 3.0     # seconds for one slow fade up and down
SENSITIVITY = 0.4    # vibration that triggers flashing (m/s^2); lower = more sensitive
FLASH_HOLD = 2.0     # seconds of flashing after the last vibration
FLASH_SPEED = 0.17   # seconds per colour: 3 flashes per second, the photosensitivity limit

GREEN = (40, 255, 0)
ORANGE = (255, 55, 0)

cp.pixels.auto_write = False
cp.pixels.brightness = BRIGHTNESS


def show(color, level):
    cp.pixels.fill((int(color[0] * level), int(color[1] * level), int(color[2] * level)))
    cp.pixels.show()


time.sleep(1)                        # let the board settle after power-up
base = list(cp.acceleration)
last_vibration = -100.0

while True:
    now = time.monotonic()
    x, y, z = cp.acceleration
    if abs(x - base[0]) + abs(y - base[1]) + abs(z - base[2]) > SENSITIVITY:
        last_vibration = now
    # the baseline slowly follows gravity and tilt, not quick vibrations
    base[0] += (x - base[0]) * 0.05
    base[1] += (y - base[1]) * 0.05
    base[2] += (z - base[2]) * 0.05

    if now - last_vibration < FLASH_HOLD:
        # flashy: orange and green alternate at full brightness
        show(ORANGE if int(now / FLASH_SPEED) % 2 else GREEN, 1)
    else:
        # calm: fade up and down; the colour changes while the light is out
        pulse = now / PULSE_TIME
        show(GREEN if int(pulse) % 2 == 0 else ORANGE,
             (1 - math.cos(2 * math.pi * pulse)) / 2)
    time.sleep(0.01)
