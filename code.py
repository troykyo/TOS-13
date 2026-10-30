# Haunted Pumpkin Guard -- Halloween table game for the
# Adafruit Circuit Playground Express (CircuitPython 8 or 9).
#
# Lay the board on the candy table. It glows like a lit pumpkin.
# When someone bumps or knocks the table, the accelerometer wakes it up:
# a slow glow turns flashy, then scratchy ... then BOO.
#
# Controls
#   Slide switch  left = sound on, right = silent (lights only)
#   Button A      cycle knock sensitivity (1..4 green pixels)
#   Button B      start a "Steal the Candy" round (see README)

import array
import math
import random
import time

import audiocore
import audioio
import board
from adafruit_circuitplayground import cp

# ---------------------------------------------------------------- settings
BRIGHTNESS = 0.35        # normal ring brightness (0..1)
SCARE_BRIGHTNESS = 0.8   # brightness for the BOO
GLOW_TIME = 4.5          # seconds of slow glow, then ...
FLASH_TIME = 3.0         # ... seconds of flashing, then ...
SCRATCH_TIME = 2.5       # ... seconds of scratching before the BOO (each +-20 %)
SNEAK_TIME = 8.0         # seconds a player must survive in a candy round
MIN_FLASH_GAP = 0.34     # full-ring flashes <= 3 per second (photosensitivity)
DEBUG = False            # True: print knock readings for the Mu plotter

# (hardware tap threshold, polled vibration threshold in m/s^2) per level;
# the accelerometer runs in the 8 g range, so 1 tap unit ~ 0.06 g.
SENSITIVITY = ((25, 2.5), (16, 1.6), (10, 1.0), (6, 0.6))

ORANGE = (255, 55, 0)
GREEN = (40, 255, 0)
PALE = (255, 160, 60)
OFF = (0, 0, 0)
EYES = (2, 7)            # two opposite pixels act as glowing eyes

cp.pixels.auto_write = False
cp.pixels.brightness = BRIGHTNESS

# ---------------------------------------------------------------- sound
# cp.start_tone() cannot change pitch while a tone is playing, so the game
# drives the speaker itself: a looped sine wave for tones, and a loop of
# random bytes that sounds like claws scratching.
speaker = audioio.AudioOut(board.SPEAKER)
amp = getattr(cp, "_speaker_enable", None)   # amplifier pin, owned by cp
SINE = audiocore.RawSample(array.array(
    "H", [int(32768 + 20000 * math.sin(math.pi * i / 16)) for i in range(32)]))
_noise = bytearray(512)
for _i in range(512):
    _noise[_i] = 64 + random.getrandbits(7)
NOISE = audiocore.RawSample(_noise)
playing = 0              # Hz of the current tone, -rate for noise, 0 = silent


def _play(sample, rate, what):
    global playing
    if cp.switch and what != playing:
        speaker.stop()               # a new sample rate only applies on play()
        sample.sample_rate = rate
        if amp:
            amp.value = True
        speaker.play(sample, loop=True)
        playing = what


def tone(freq):
    _play(SINE, 32 * int(freq), int(freq))


def scratch(rate):
    _play(NOISE, rate, -rate)


def quiet(amp_off=False):
    global playing
    if playing:
        speaker.stop()
        playing = 0
    if amp_off and amp:
        amp.value = False            # as cp.stop_tone() does between sounds


# ---------------------------------------------------------------- sensing
level = 1
base = [0.0, 0.0, 0.0]


def set_sensitivity():
    try:
        # Lower threshold than the library default (90) so a knock on the
        # table, not only a tap on the board itself, is latched in hardware.
        cp._lis3dh.set_tap(1, SENSITIVITY[level][0],
                           time_limit=10, time_latency=20, time_window=255)
    except (AttributeError, TypeError):
        cp.detect_taps = 1


def rearm(settle=1.2):
    """Wait for the board to stop moving, then take a fresh baseline."""
    global base
    quiet(amp_off=True)
    time.sleep(settle)
    base = list(cp.acceleration)
    cp.tapped  # reading clears a latched tap


def knocked():
    """True on a knock (latched by the accelerometer) or ongoing shaking."""
    x, y, z = cp.acceleration
    d = abs(x - base[0]) + abs(y - base[1]) + abs(z - base[2])
    # a slow baseline follows gravity and tilt, but not sharp knocks
    base[0] += (x - base[0]) * 0.05
    base[1] += (y - base[1]) * 0.05
    base[2] += (z - base[2]) * 0.05
    tap = cp.tapped
    if DEBUG:
        print((d, SENSITIVITY[level][1], 5 if tap else 0))
    return tap or d > SENSITIVITY[level][1]


# ---------------------------------------------------------------- lights
def scale(c, k):
    k = 0.0 if k < 0 else 1.0 if k > 1 else k
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def ring(c):
    cp.pixels.fill(c)
    cp.pixels.show()


def embers(lo, hi):
    """Every pixel a randomly flickering orange between lo and hi."""
    for i in range(10):
        cp.pixels[i] = scale(ORANGE, lo + (hi - lo) * random.random())


def eyes(c):
    cp.pixels[EYES[0]] = c
    cp.pixels[EYES[1]] = c


next_eyes = time.monotonic() + 5
eyes_until = 0.0


def idle_frame(t):
    """Flickering jack-o'-lantern glow; now and then the eyes open."""
    global next_eyes, eyes_until
    breath = 0.25 + 0.12 * math.sin(t * 1.7)
    embers(0.7 * breath, breath)
    if t >= next_eyes:
        eyes_until = t + 1.6
        next_eyes = t + random.uniform(6, 14)
    if t < eyes_until:
        eyes(OFF if 0.7 < eyes_until - t < 0.82 else GREEN)
    cp.pixels.show()


# ---------------------------------------------------------------- the scare
def heartbeat(b):
    """Sound b seconds into a heartbeat: 'lub' ... 'dub' ... silence."""
    if b < 0.07:
        tone(150)
    elif 0.15 <= b < 0.22:
        tone(120)
    else:
        quiet()


def slow_glow(duration):
    """The light sinks, then swells slowly; eyes open; a heart starts beating."""
    t0 = time.monotonic()
    beat = t0 - 0.5                        # first beat after ~0.8 s of dark
    while True:
        now = time.monotonic()
        p = (now - t0) / duration          # 0 -> 1 across the phase
        if p >= 1:
            return
        if now - beat > 1.3 - 0.5 * p:     # the heart speeds up
            beat = now
        heartbeat(now - beat)
        glow = p * p + 0.15 * max(0.0, 1 - (now - beat) / 0.3)
        embers(0.8 * glow, glow)
        if p > 0.4:
            eyes(scale(GREEN, (p - 0.4) / 0.6))
        cp.pixels.show()
        time.sleep(0.02)


def flashy(duration):
    """Each ever faster beat flashes the ring, orange and green in turn, with a
    sting a tritone apart; between flashes the embers flicker harder."""
    t0 = beat = time.monotonic()
    n = 0
    while True:
        now = time.monotonic()
        p = (now - t0) / duration
        if p >= 1:
            return
        if now - beat >= max(MIN_FLASH_GAP, 0.7 - 0.4 * p):
            beat = now
            n += 1
        k = 1 - (now - beat) / 0.25        # each flash fades in 0.25 s
        if k > 0.3:
            cp.pixels.fill(scale(GREEN if n % 2 else ORANGE, k))
            tone(1245 if n % 2 else 880)
        else:
            quiet()
            embers(0.1, 0.4)
            eyes(GREEN)
        cp.pixels.show()
        time.sleep(0.02)


def scratchy(duration):
    """Claws: bursts of scratching noise while streaks rake the ring."""
    end = time.monotonic() + duration
    while time.monotonic() < end:
        pos = random.randint(0, 9)
        step = random.choice((1, -1))
        scratch(random.randint(5000, 14000))
        for _ in range(random.randint(3, 6)):
            embers(0.05, 0.15)
            cp.pixels[pos % 10] = PALE
            cp.pixels[(pos - step) % 10] = GREEN
            cp.pixels[(pos - 2 * step) % 10] = scale(GREEN, 0.3)
            cp.pixels.show()
            pos += step
            time.sleep(0.03)
        quiet()
        embers(0.05, 0.15)
        cp.pixels.show()
        time.sleep(random.uniform(0.03, 0.25))


def boo():
    """Sudden darkness and silence ... BOO ... and a slow cackle."""
    quiet()
    ring(OFF)
    time.sleep(random.uniform(0.6, 1.4))   # an unpredictable pause
    cp.pixels.brightness = SCARE_BRIGHTNESS
    ring(ORANGE)
    for f in range(1800, 300, -50):        # screech
        tone(f)
        time.sleep(0.01)
    for k in range(20):
        ring(scale(ORANGE, 1 - k / 20))
        tone(300 - 10 * k)
        time.sleep(0.03)
    cp.pixels.brightness = BRIGHTNESS
    for n in range(6):                     # "HA ... HA ... HA": the halves
        for i in range(10):                # swap at most 3 times a second
            cp.pixels[i] = ORANGE if (i < 5) == (n % 2 == 0) else GREEN
        cp.pixels.show()
        tone(523 - 26 * n)
        time.sleep(0.2)
        quiet()
        time.sleep(MIN_FLASH_GAP - 0.2)
    ring(OFF)


def awaken():
    """The accelerometer woke it: slow glow -> flashy -> scratchy -> BOO."""
    j = random.uniform(0.8, 1.2)           # never quite the same length twice
    slow_glow(GLOW_TIME * j)
    flashy(FLASH_TIME * j)
    scratchy(SCRATCH_TIME * j)
    boo()


# ---------------------------------------------------------------- game
def win():
    for f in (523, 659, 784, 1047):
        for _ in range(4):
            cp.pixels.fill(OFF)
            cp.pixels[random.randint(0, 9)] = GREEN
            cp.pixels[random.randint(0, 9)] = scale(ORANGE, 0.5)
            cp.pixels.show()
            tone(f)
            time.sleep(0.05)
    quiet()
    ring(scale(GREEN, 0.4))
    time.sleep(0.6)
    ring(OFF)


def show_level():
    cp.pixels.fill(OFF)
    for i in range(level + 1):
        cp.pixels[i] = GREEN
    cp.pixels.show()
    tone(300 + 150 * level)
    time.sleep(0.15)
    quiet()
    time.sleep(0.8)


def candy_round():
    """Countdown, then the player must grab candy without shaking the table.
    Every survived round makes the pumpkin more sensitive."""
    global level
    for n in range(3):
        cp.pixels.fill(OFF)
        for i in range(n * 3 + 3):
            cp.pixels[i] = scale(GREEN, 0.5)
        cp.pixels.show()
        tone(660)
        time.sleep(0.15)
        quiet()
        time.sleep(0.6)
    rearm(0.3)                    # ignore the button-press wobble
    t0 = time.monotonic()
    while True:
        left = SNEAK_TIME - (time.monotonic() - t0)
        if left <= 0:
            break
        if knocked():
            awaken()
            level = 1
            set_sensitivity()
            return
        lit = math.ceil(left / SNEAK_TIME * 10)
        for i in range(10):
            cp.pixels[i] = scale(ORANGE, 0.4) if i < lit else OFF
        cp.pixels.show()
        time.sleep(0.01)
    win()
    if level < len(SENSITIVITY) - 1:
        level += 1
        set_sensitivity()
    show_level()


# ---------------------------------------------------------------- main
set_sensitivity()
ring(OFF)
rearm(2.0)
prev_a = prev_b = False

while True:
    a, b = cp.button_a, cp.button_b
    if a and not prev_a:
        level = (level + 1) % len(SENSITIVITY)
        set_sensitivity()
        show_level()
        rearm()
    elif b and not prev_b:
        candy_round()
        rearm()
    elif knocked():
        awaken()
        rearm(2.0)
    else:
        idle_frame(time.monotonic())
    prev_a, prev_b = a, b
    time.sleep(0.02)
