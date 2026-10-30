# Halloween pumpkin light for the Adafruit Circuit Playground Express
# (CircuitPython). Uses only the board itself: the 10 onboard LEDs and
# the onboard accelerometer. No buttons, no sound, nothing attached.
#
# Calm: the LEDs slowly fade up and down -- green, then orange, then
# green, then orange.
# A vibration well above the 60-second rolling average: the LEDs flash
# orange and green for 3 seconds.

import math
import time

from adafruit_circuitplayground import cp

BRIGHTNESS = 0.3     # LED brightness (0..1)
PULSE_TIME = 3.0     # seconds for one slow fade up and down
SENSITIVITY = 4.0    # flash when a vibration is 4x the rolling average; lower = more sensitive
AVERAGE_TIME = 60    # seconds in the rolling average; older readings are flushed
FLASH_TIME = 3.0     # seconds of flashing per detected vibration
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
sums = [0.0] * AVERAGE_TIME          # vibration readings added up per second ...
counts = [0] * AVERAGE_TIME          # ... and how many readings that second
second = int(time.monotonic()) % AVERAGE_TIME
average = 100.0                      # no flashing until the first second is measured
flash_until = 0.0

while True:
    now = time.monotonic()
    x, y, z = cp.acceleration
    vibration = abs(x - base[0]) + abs(y - base[1]) + abs(z - base[2])
    # the baseline slowly follows gravity and tilt, not quick vibrations
    base[0] += (x - base[0]) * 0.05
    base[1] += (y - base[1]) * 0.05
    base[2] += (z - base[2]) * 0.05

    # rolling average: each new second flushes the second from AVERAGE_TIME ago
    s = int(now) % AVERAGE_TIME
    if s != second:
        second = s
        sums[s] = 0.0
        counts[s] = 0
        average = sum(sums) / max(1, sum(counts))
    sums[s] += vibration
    counts[s] += 1

    # max(..., 0.04): never react to a single accelerometer step (~0.04 m/s^2)
    if now >= flash_until and vibration > SENSITIVITY * max(average, 0.04):
        flash_until = now + FLASH_TIME

    if now < flash_until:
        # flashy: orange and green alternate at full brightness
        show(ORANGE if int(now / FLASH_SPEED) % 2 else GREEN, 1)
    else:
        # calm: fade up and down; the colour changes while the light is out
        pulse = now / PULSE_TIME
        show(GREEN if int(pulse) % 2 == 0 else ORANGE,
             (1 - math.cos(2 * math.pi * pulse)) / 2)
    time.sleep(0.01)
