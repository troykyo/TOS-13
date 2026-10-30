# TOS-13 — Haunted Pumpkin Guard

A Halloween table game for the **Adafruit Circuit Playground Express (CPX)**.
Put the board on the candy table: it glows like a flickering jack-o'-lantern.
Bump or knock the table, or lean over the board, and it wakes up with an
orange and green light show and spooky sounds.

## Setup
1. Install CircuitPython 9.x for the CPX (circuitpython.org/board/circuitplayground_express).
   The `adafruit_circuitplayground` library is already built into that firmware.
2. Copy `code.py` to the `CIRCUITPY` drive. It starts automatically.
3. Power it from USB or a 3×AAA pack. Tape it face-up **directly on the table top**:
   vibration travels much better through a hard surface than through a tablecloth.

## Controls
| Control | Function |
|---|---|
| Slide switch | left = sound on, right = lights only |
| Button A | sensitivity 1–4 (shown as green pixels) |
| Button B | start a **Steal the Candy** round |

## How the scare works
When someone knocks the table or leans over the board, it plays two random effects, then a jump-scare, then a cackle:
- **Heartbeat**: a green "lub-dub" that keeps getting faster
- **Comet**: an orange head with a green tail, spinning faster and faster as the pitch rises
- **Lightning**: darkness, a pale flash, orange afterglow and thunder
- **Possessed**: pixels twitch between orange, green and dark while the board chatters
- **Jump-scare**: green eyes slowly open in the dark, a random silent pause, then a burst of orange and a screech

Because the order and timing change every time, people can't learn the pattern.

## Game: Steal the Candy
Press **B**. After a three-beep countdown, the player has 8 s (the orange ring
counts down) to take candy **without shaking the table**. If they survive,
everything turns green and the pumpkin gets more sensitive for the next round.
If they shake it, they get the full scare and the level resets to 1.

## Tuning (top of `code.py`)
- `SENSITIVITY`: knock thresholds per level. If it triggers by itself, use a lower level or raise the values.
- `SHADOW_TRIGGER`: set to `False` in very dark rooms. The board's own LEDs light up its light sensor there, so shadow detection becomes unreliable.
- `SNEAK_TIME`, `BRIGHTNESS`, `SCARE_BRIGHTNESS`.

## Safety
Full-ring flashes are kept to **3 per second or fewer** (`MIN_FLASH_GAP`). This follows the
general-flash threshold in WCAG 2.x 2.3.1 / EN 301 549, to reduce the risk for
people with photosensitive epilepsy. Do not lower it. Put up a small "flashing lights & sudden
sounds" notice at the table, especially if young children will be there.
