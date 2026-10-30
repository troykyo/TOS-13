# TOS-13 — Haunted Pumpkin Guard

A Halloween table game for the **Adafruit Circuit Playground Express (CPX)**.
Put the board on the candy table: it glows like a flickering jack-o'-lantern.
When someone bumps or knocks the table, the accelerometer wakes it up. It starts
as a slow glow, turns flashy, then scratchy, and ends with a BOO.

## What happens after a knock (about 13 s)
| Phase | Length | Light | Sound |
|---|---|---|---|
| **Slow glow** | ~4.5 s | the pumpkin goes dark, then swells slowly to deep orange while green eyes open | a heartbeat that speeds up |
| **Flashy** | ~3 s | every beat flashes the ring orange or green in turn, with embers flickering between flashes | short stings a tritone apart |
| **Scratchy** | ~2.5 s | streaks rake around the ring like claw marks | bursts of scratching noise |
| **BOO** | ~3 s | darkness and silence for a random pause, then a bright orange burst and a slow cackle | screech, then "HA… HA… HA" |

Each run is 80–120 % of these lengths, and the details are random, so people can't learn the pattern.
Phase lengths are set by `GLOW_TIME`, `FLASH_TIME` and `SCRATCH_TIME`.

## Setup
1. Install CircuitPython 9.x for the CPX (circuitpython.org/board/circuitplayground_express).
   The `adafruit_circuitplayground` library is already built into that firmware.
2. Copy `code.py` to the `CIRCUITPY` drive. It restarts automatically.
3. Tape the board face-up **directly on the table top**. Vibration travels much better
   through a hard surface than through a tablecloth. Tape the USB or battery cable down too,
   so that tugging the cable doesn't set it off.

## Controls
| Control | Function |
|---|---|
| Slide switch | left = sound on, right = lights only |
| Button A | knock sensitivity 1–4 (shown as green pixels, starts at 2) |
| Button B | start a **Steal the Candy** round |

## First test on the hardware
1. Open the serial console (Mu editor → *Serial*, or any terminal at 115200 baud) to see errors.
2. Wait about 2 s after a restart. The board settles first, then the orange glow begins.
3. Knock on the table about 30 cm from the board. If nothing happens, press **A** for a
   higher level. If it goes off by itself, choose a lower level.
4. If it's silent, flip the slide switch.
5. To fine-tune, set `DEBUG = True` and open Mu's *Plotter*. It shows the vibration reading,
   the current threshold, and a spike for each knock the accelerometer latched.
6. If you see `MemoryError`, report it. The program is about 4 KB when compiled,
   but the CPX has very little RAM.

## Game: Steal the Candy
Press **B**. After a three-beep countdown, the player has 8 s (the orange ring
counts down) to take candy **without shaking the table**. If they survive, the ring
sparkles green and the pumpkin becomes more sensitive for the next round. If they
shake it, the full scare plays and the level resets.

## Tuning (top of `code.py`)
- `SENSITIVITY`: knock thresholds per level (hardware tap latch and polled vibration).
- `GLOW_TIME`, `FLASH_TIME`, `SCRATCH_TIME`, `SNEAK_TIME`, `BRIGHTNESS`, `SCARE_BRIGHTNESS`.

## Safety
- The whole ring never flashes more than **3 times per second** (`MIN_FLASH_GAP`). The cackle
  swaps its two halves at the same limit. This follows the general-flash threshold in WCAG 2.x
  2.3.1 / EN 301 549, to reduce the risk for people with photosensitive epilepsy.
  Don't lower `MIN_FLASH_GAP`.
- The scratchy phase uses small moving streaks (3 of the 10 LEDs), not whole-ring flashes.
- Put up a small "flashing lights & sudden sounds" notice at the table, especially if young
  children will be there.
