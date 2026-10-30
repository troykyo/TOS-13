# Haunted Pumpkin Guard -- Halloween table game for the
# Adafruit Circuit Playground Express (CircuitPython 8/9).
#
# Lay the board on the candy table. It glows like a lit pumpkin.
# Anyone who bumps or knocks the table (or casts a shadow over the board)
# wakes it up: orange/green light show + spooky sounds.
#
# Controls
#   Slide switch  left = sound on, right = silent (lights only)
#   Button A      cycle vibration sensitivity (1..4 green pixels)
#   Button B      start a "Steal the Candy" round (see README)

import math
import random
import time

try:
    from adafruit_circuitplayground import cp
except ImportError:  # very old firmware
    from adafruit_circuitplayground.express import cpx as cp

# ---------------------------------------------------------------- settings
BRIGHTNESS = 0.35        # normal ring brightness (0..1)
SCARE_BRIGHTNESS = 0.8   # brightness for the final jump-scare
SHADOW_TRIGGER = True    # also wake up when someone leans over the board
SHADOW_RATIO = 0.55      # light must drop below 55 % of the ambient level
SNEAK_TIME = 8.0         # seconds a player must survive in a round
MIN_FLASH_GAP = 0.34     # full-ring flashes <= 3 per second (photosensitivity)

# (hardware tap threshold, polled vibration threshold in m/s^2) per level;
# the accelerometer runs in the 8 g range, so 1 tap unit ~ 0.06 g.
SENSITIVITY = ((25, 2.5), (16, 1.6), (10, 1.0), (6, 0.6))

ORANGE = (255, 55, 0)
GREEN = (40, 255, 0)
PALE = (255, 160, 60)
OFF = (0, 0, 0)
EYES = (2, 7)            # two opposite pixels act as glowing eyes

# ---------------------------------------------------------------- helpers
cp.pixels.auto_write = False
cp.pixels.brightness = BRIGHTNESS


def scale(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def fill(c):
    cp.pixels.fill(c)
    cp.pixels.show()


def tone(freq):
    if cp.switch:
        cp.start_tone(int(freq))


def quiet():
    cp.stop_tone()


# ---------------------------------------------------------------- sensing
level = 1
accel_base = [0.0, 0.0, 0.0]
light_base = 0.0
dark_count = 0


def set_sensitivity():
    cp.detect_taps = 1
    try:
        # Lower threshold than the library default so a knock on the table,
        # not only a tap on the board itself, is latched in hardware.
        cp._lis3dh.set_tap(1, SENSITIVITY[level][0],
                           time_limit=10, time_latency=20, time_window=255)
    except (AttributeError, TypeError):
        pass


def rearm(settle=1.2):
    """Wait for the board to stop moving, then take fresh baselines."""
    global light_base, dark_count
    quiet()
    time.sleep(settle)
    x, y, z = cp.acceleration
    accel_base[0], accel_base[1], accel_base[2] = x, y, z
    light_base = cp.light
    dark_count = 0
    cp.tapped  # reading clears any latched tap


def vibration():
    x, y, z = cp.acceleration
    d = abs(x - accel_base[0]) + abs(y - accel_base[1]) + abs(z - accel_base[2])
    # slow-moving baseline follows gravity/tilt but not sharp knocks
    accel_base[0] += (x - accel_base[0]) * 0.05
    accel_base[1] += (y - accel_base[1]) * 0.05
    accel_base[2] += (z - accel_base[2]) * 0.05
    return cp.tapped or d > SENSITIVITY[level][1]


def shadow():
    global light_base, dark_count
    lux = cp.light
    if light_base > 15 and lux < light_base * SHADOW_RATIO:
        dark_count += 1          # require a sustained drop, not one noisy read
        return dark_count >= 3
    dark_count = 0
    light_base += (lux - light_base) * 0.01
    return False


# ---------------------------------------------------------------- effects
def heartbeat(beats=6):
    """Sickly green 'lub-dub' that speeds up; never drops to full dark."""
    period = 1.0
    for _ in range(beats):
        t0 = time.monotonic()
        for peak, freq in ((1.0, 110), (0.55, 90)):
            tone(freq)
            for k in range(8):
                fill(scale(GREEN, 0.12 + peak * 0.88 * (1 - k / 8)))
                time.sleep(0.015)
            quiet()
            time.sleep(0.06)
        fill(scale(GREEN, 0.12))
        time.sleep(max(MIN_FLASH_GAP, period - (time.monotonic() - t0)))
        period *= 0.8


def comet(duration=3.0):
    """Orange head with a green ghost tail, spinning faster and faster."""
    pos, speed = 0.0, 5.0
    t0 = last = time.monotonic()
    while time.monotonic() - t0 < duration:
        now = time.monotonic()
        speed = min(speed * 1.04, 45.0)
        pos += speed * (now - last)
        last = now
        head = int(pos) % 10
        for i in range(10):
            dist = (head - i) % 10
            if dist == 0:
                cp.pixels[i] = ORANGE
            elif dist < 4:
                cp.pixels[i] = scale(GREEN, (4 - dist) / 5)
            else:
                cp.pixels[i] = OFF
        cp.pixels.show()
        tone(180 + speed * 25)
        time.sleep(0.01)
    quiet()


def lightning(strikes=3):
    """Darkness, a pale flash, orange afterglow and rumbling thunder."""
    for _ in range(strikes):
        fill(OFF)
        time.sleep(random.uniform(0.4, 1.0))
        fill(PALE)
        tone(random.randint(900, 1400))
        time.sleep(0.06)
        t0 = time.monotonic()
        dt = 0.0
        while dt < 0.6:
            dt = time.monotonic() - t0
            fill(scale(ORANGE, max(0.0, 0.5 - dt)))
            tone(random.randint(60, 160))
            time.sleep(0.03)
        quiet()
        time.sleep(MIN_FLASH_GAP)


def possessed(duration=2.0):
    """Pixels twitch between orange, green and dark; the board 'chatters'."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < duration:
        i = random.randint(0, 9)
        cp.pixels[i] = random.choice((ORANGE, GREEN, OFF, OFF))
        cp.pixels.show()
        tone(random.choice((233, 311, 466, 622, 932)))
        time.sleep(random.uniform(0.03, 0.09))
    quiet()


def eyes_jumpscare():
    """Green eyes slowly open in the dark, silence ... then BOO."""
    fill(OFF)
    steps = 40
    for k in range(steps):
        c = scale(GREEN, (k + 1) / steps)
        cp.pixels[EYES[0]] = c
        cp.pixels[EYES[1]] = c
        cp.pixels.show()
        tone(420 - k * 6)
        time.sleep(0.05)
    quiet()
    time.sleep(random.uniform(0.6, 1.4))   # unpredictable pause = real fright
    cp.pixels.brightness = SCARE_BRIGHTNESS
    fill(ORANGE)
    for f in range(1800, 300, -60):
        tone(f)
        time.sleep(0.008)
    for k in range(20):
        fill(scale(ORANGE, 1 - k / 20))
        tone(300 - k * 10)
        time.sleep(0.03)
    quiet()
    cp.pixels.brightness = BRIGHTNESS


def cackle():
    """Alternate the two halves orange/green while the board laughs."""
    for n in range(8):
        a, b = (ORANGE, GREEN) if n % 2 else (GREEN, ORANGE)
        for i in range(10):
            cp.pixels[i] = a if i < 5 else b
        cp.pixels.show()
        tone(520 if n % 2 else 440)
        time.sleep(0.12)
        quiet()
        time.sleep(0.04)
    fill(OFF)


def scare():
    effects = [heartbeat, comet, lightning, possessed]
    first = random.choice(effects)
    effects.remove(first)
    first()
    random.choice(effects)()
    eyes_jumpscare()
    cackle()
    quiet()


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
    fill(scale(GREEN, 0.4))
    time.sleep(0.6)
    fill(OFF)


def show_level():
    cp.pixels.fill(OFF)
    for i in range(level + 1):
        cp.pixels[i] = GREEN
    cp.pixels.show()
    tone(300 + 150 * level)
    time.sleep(0.15)
    quiet()
    time.sleep(0.8)


# ---------------------------------------------------------------- game
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
        if vibration():
            scare()
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


next_eyes = time.monotonic() + 5
eyes_until = 0.0


def idle_frame(t):
    """Flickering jack-o'-lantern glow; now and then the eyes open."""
    global next_eyes, eyes_until
    breath = 0.25 + 0.12 * math.sin(t * 1.7)
    for i in range(10):
        cp.pixels[i] = scale(ORANGE, breath * (0.7 + 0.3 * random.random()))
    if t >= next_eyes:
        eyes_until = t + 1.6
        next_eyes = t + random.uniform(6, 14)
    if t < eyes_until:
        blink = 0.7 < eyes_until - t < 0.82
        for e in EYES:
            cp.pixels[e] = OFF if blink else GREEN
    cp.pixels.show()


# ---------------------------------------------------------------- main
set_sensitivity()
fill(OFF)
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
    elif vibration() or (SHADOW_TRIGGER and shadow()):
        scare()
        rearm(2.0)
    else:
        idle_frame(time.monotonic())
    prev_a, prev_b = a, b
    time.sleep(0.02)
