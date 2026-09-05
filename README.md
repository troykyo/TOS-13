# TOS-13

Tools for mapping and maintaining a professional network in the textile
ecosystem. Local-first, EU-compliant by construction, no server component.

## Tools

| Tool | Stage | Status |
|---|---|---|
| [`tools/contact-rank`](tools/contact-rank) | Who actually matters — the contact graph, derived from local macOS stores | **Working** |
| Profile resolution | Associating a contact with their public professional profile | Designed |
| Image acquisition | Retrieving a contact photograph, human-confirmed | Designed, decision pending |

## Documents

- [Proposal 0001 — Contact graph and profile enrichment](docs/proposals/0001-contact-graph-and-profile-enrichment.md)
  — the three stages, the scoring model, platform constraints, the EU legal
  analysis and the design rules that follow from it.
- [GDPR assessment 0001](docs/legal/0001-gdpr-assessment.md) — legitimate
  interests assessment, Article 14 notice, retention and data-subject rights.

## Start here

```sh
cd tools/contact-rank
python3 contactrank.py probe
python3 contactrank.py rank --top 50
```

## Principles

These are constraints on every tool in this repository, not aspirations.
Proposal 0001 §5 sets out where each comes from.

1. No face recognition, matching, clustering or deduplication on stored images.
2. Enrich only people with an evidenced prior interaction. No speculative lookups.
3. A human confirms every identity match and every save, by default.
4. Never evade a technical control. A challenge stops the run.
5. Every stored record carries provenance, a timestamp, a TTL and a working erase.
6. Nothing leaves the machine without an explicit, per-destination decision.
