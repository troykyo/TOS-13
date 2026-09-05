# contact-rank

Ranks the people you actually communicate with, from the stores macOS already
keeps on your Mac. Stage A of the TOS-13 relationship-graph toolchain
(see `docs/proposals/0001-contact-graph-and-profile-enrichment.md`).

Read-only, offline, standard library only. Nothing leaves the machine, nothing
is written unless you ask for it, and the source databases are never modified —
each is snapshotted to a temporary directory and read there.

## Why not just count emails

Counting messages ranks your newsletters above your collaborators. This tool
weights each interaction by channel and direction, damps messages sent to large
audiences, decays with a 180-day half-life, and multiplies by a reciprocity term
so that a two-way correspondence outranks a one-way feed of the same volume.
The model is set out in proposal §2.3.

## Use

```sh
python3 contactrank.py probe                       # what is readable, and what is not
python3 contactrank.py rank --top 50               # print the ranking
python3 contactrank.py rank --out ranking.csv      # full ranking to CSV
python3 contactrank.py rank --since 365            # present tense only
python3 contactrank.py rank --half-life 90         # weight the last quarter harder
python3 contactrank.py rank --sources calendar     # meetings alone
```

Start with `probe`. It reports which of the six stores exist, how many rows each
holds, and what is blocking access.

## Full Disk Access

Every source except Contacts is gated by macOS TCC. Grant Full Disk Access to
the **terminal application** you are running this from — Terminal, iTerm, your
IDE — not to the script:

> System Settings → Privacy & Security → Full Disk Access → **+**

Then quit and reopen that application; the grant is read at launch. Without it,
`probe` reports `permission denied` per store and `rank` silently falls back to
whatever it can read.

## Sources

| Source | What it contributes |
|---|---|
| Mail | Direction, recipients, per-message audience size |
| Messages | iMessage and SMS, group membership |
| Calls | FaceTime and iPhone calls via Continuity |
| Calendar | Meeting co-attendance — the strongest professional signal |
| Contacts | Names, organisations, titles, existing LinkedIn URLs and photos |
| CoreDuet | Apple's own interaction ledger. Opt-in, `--include-coreduet` |

CoreDuet (`interactionC.db`) is what feeds Siri's people suggestions, and is the
literal system answer to "who do I contact most". It is also undocumented and
its direction encoding varies between macOS releases, so it is off by default
and weighted as corroboration rather than evidence.

Mail will be empty if you use Spark, Outlook or a browser client — the other
four sources still carry the signal.

## Output columns

`score` is tie strength; `reciprocity` is 0 for a one-way relationship and 1 for
a balanced one; `active_days` counts distinct days of contact, which is the best
single indicator that a tie is real. `linkedin_url` and `has_photo` come from
the address book and are the input to the next stage — check them before
building anything that goes near the network.

`--out` and `--json` write names, addresses and phone numbers in the clear.
Choose the destination deliberately and treat the file as you would the address
book itself. It is not written unless you ask.

## Compatibility

Python 3.9+ (macOS ships 3.9), no dependencies. macOS 12–15 schemas; every
extractor introspects the live schema and degrades to a skipped source rather
than a crash if Apple renames something. `probe` reports what it actually found.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

46 tests. The macOS stores cannot exist on CI, so each extractor is exercised
against a synthetic SQLite fixture built to the same column shape as the real
store, plus an end-to-end CLI test against a complete synthetic home directory.
That verifies the SQL, the epoch handling, the direction logic and the scoring;
it cannot verify that Apple has not changed a schema, which is what `probe` is
for.
