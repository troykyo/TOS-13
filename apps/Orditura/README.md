# Orditura

The Mac app for Stage A: who you actually communicate with, derived from the
six stores macOS already keeps. Swift port of `tools/contact-rank`, with the
same scoring model and the same CSV output.

*Orditura* is the warp — the fixed threads a fabric is built on. The contact
graph is the warp of everything else in TOS-13.

## Status

**Not yet compiled.** This was written in an environment with no Swift
toolchain (`download.swift.org` is blocked by the sandbox's network policy), so
the code has been reviewed but not built. Run the tests first — they are the
verification story, and they mirror the Python suite case for case so the two
implementations can be checked against each other on the same machine:

```sh
cd apps/Orditura
swift test
```

Expect to fix a few compile errors on the first pass. Send them over and I'll
work through them.

## Build and run

```sh
./Scripts/bundle.sh          # builds and assembles .build/Orditura.app
```

SwiftPM produces a bare executable; the script wraps it in a bundle so the app
has a stable identity. That identity is what a Full Disk Access grant attaches
to — run the raw executable and you would re-grant access every time the binary
moved.

Then, **before first launch**:

> System Settings → Privacy & Security → Full Disk Access → add Orditura

Move the app where you want it *first*: relocating it after granting access
invalidates the grant.

## Why it cannot be sandboxed

Every store except Contacts sits behind `kTCCServiceSystemPolicyAllFiles`, and
an App-Sandboxed process can never hold Full Disk Access. So the app must be
non-sandboxed, Developer ID–signed and notarised, and distributed outside the
Mac App Store. That is a consequence of the data sources, not a preference —
see `docs/proposals/0001` §2.2. `Scripts/bundle.sh` signs ad-hoc, which is
enough for your own machine and not enough for anyone else's.

## Layout

```
Sources/OrdituraCore/          pure Foundation + SQLite3 — no AppKit, no SwiftUI
  Identity.swift               normalisation, role-address detection, union-find
  Model.swift                  Event, Card, Person
  Scoring.swift                weights, damping, decay, reciprocity, the ranker
  SQLiteStore.swift            snapshotting read-only reader, schema introspection
  StoreLocations.swift         where the six stores live; the access probe
  ContactGraph.swift           orchestration, per-source reporting
  Export.swift                 CSV and JSON, byte-identical to contact-rank
  Extractors/                  one per store
Sources/Orditura/              SwiftUI shell
Tests/OrdituraCoreTests/       fixtures + the full suite
```

The split is the point: Stage A is testable from the command line without a
running app, without a window, and without any permission prompt beyond file
access. Everything that needs AppKit lives in the thin shell above it.

## What the app does

- **Access gate.** There is no API to request Full Disk Access, so the app
  detects the failure per store, names what it could not read, and opens the
  right Settings pane. Anything more confident would be a lie.
- **Ranking.** Sorted by tie strength, with reciprocity stated in words — a
  "one-sided" relationship and a "balanced" one at the same message count are
  very different things, and that distinction is the whole reason this is not a
  message counter.
- **Window and half-life** are live controls. Narrow the window for the
  present-tense question; widen it to surface people who have gone quiet.
- **"Needs a photo"** filters to contacts with no image on file. That list, plus
  the `linkedin_url` column, is the input to Stage B.
- **Export** writes exactly what is on screen, after the current search and
  filters, and says plainly that the file is personal data in the clear.

## Parity with contact-rank

Both implementations produce the same columns in the same order, with the same
formatting (booleans lowercase, floats at three decimal places), so a run of
each on the same Mac can be diffed directly. If they disagree, one of them is
wrong and the diff says where.
