# Proposal 0001 — Contact graph and profile enrichment

| | |
|---|---|
| **Status** | Stage A implemented (CLI + Mac app). Stage C posture decided: **C3**. Stage B pending |
| **Date** | 2026-09-05 |
| **Scope** | TOS-13 tool family, first tool |
| **Decision owners** | Design engineering + whoever acts as controller for the studio's contact data |

---

## 1. What was asked, and what this proposes

The request: a small Mac application that walks the address book, finds each
person's LinkedIn profile, and retrieves their profile photograph — slowly,
through the web interface, so that traffic resembles a person browsing rather
than a crawler. The starting point named in the request was the observation
that macOS already knows who you are in touch with most.

That instinct is correct, and it is the load-bearing part of the idea. The rest
of the request decomposes into three problems that are usually conflated and
should not be, because they differ enormously in difficulty, in value, and in
legal exposure:

| Stage | Question | Difficulty | Risk | Status |
|---|---|---|---|---|
| **A** | Who actually matters to me? | Moderate — undocumented schemas | **None** — local, read-only | **Implemented** (`tools/contact-rank`, `apps/Orditura`) |
| **B** | Which LinkedIn profile is this person? | Hard — entity resolution | Low to moderate | Designed, not built |
| **C** | How do I obtain their photograph? | Easy technically | **High** — contract, GDPR, copyright | Designed, decision required |

Stage A is the whole value of the tool and carries no risk worth discussing. It
is finished and tested. Stages B and C are where the interesting choices are,
and section 6 sets out what those choices cost.

The headline recommendation is in section 7. In short: build Stage A into the
Mac app first, resolve Stage B from data you already lawfully hold before
touching the network at all, and make Stage C human-confirmed by default.
Most of the photographs you want are obtainable without automating LinkedIn.

---

## 2. Stage A — deriving the contact graph from macOS

### 2.1 The stores

macOS keeps six local SQLite databases that, between them, describe every
interaction the machine has mediated. None of them is documented by Apple and
all of them are read-only from our point of view.

| Source | Path | What it contributes |
|---|---|---|
| Mail | `~/Library/Mail/V*/MailData/Envelope Index` | Sender, recipients, direction, timestamps, per-message audience size |
| Messages | `~/Library/Messages/chat.db` | iMessage/SMS, `is_from_me`, group membership |
| Calls | `~/Library/Application Support/CallHistoryDB/CallHistory.storedata` | FaceTime and iPhone calls via Continuity |
| Calendar | `~/Library/Calendars/Calendar.sqlitedb` | **Meeting co-attendance** — the strongest professional signal available |
| Contacts | `~/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb` | Names, organisations, job titles, existing LinkedIn URLs, existing photos |
| CoreDuet | `~/Library/Application Support/CoreDuet/People/interactionC.db` | Apple's own interaction ledger — what feeds Siri's people suggestions |

The last one is the literal answer to "there's a way to see on macOS who my
people are". `interactionC.db` is precisely that ledger, and `ZCONTACTS` even
carries pre-aggregated counters. It is also entirely undocumented, its
direction encoding has changed between releases, and its join tables are Core
Data artefacts whose names differ per machine. It is therefore **opt-in**
(`--include-coreduet`) and treated as corroborating evidence, never as ground
truth. Deriving the graph ourselves from Mail, Messages, Calls and Calendar is
both more transparent and more stable.

### 2.2 Access reality: TCC, sandboxing, and what it means for distribution

Every store above except Contacts sits behind macOS's TCC subsystem under
`kTCCServiceSystemPolicyAllFiles` — **Full Disk Access**. Three consequences,
and the third one is architectural:

1. There is no API to request Full Disk Access. The user must grant it by hand
   in System Settings → Privacy & Security. The app can only detect the failure
   and explain the remedy well.
2. When running the CLI, the grant must go to the **terminal application**
   (Terminal, iTerm, the IDE), not to the script.
3. **An App-Sandboxed application can never hold Full Disk Access.** The Mac app
   must therefore be non-sandboxed, Developer ID–signed and notarised, and
   distributed outside the Mac App Store. This is not a preference; it follows
   from the data sources. Decide it now, because it also determines the update
   mechanism (Sparkle or equivalent) and the code-signing setup.

A sandboxed, App-Store-distributable variant is possible but would be limited
to `Contacts.framework` plus whatever the user drags in manually — no
interaction ranking, which is to say, no product.

### 2.3 Scoring: what "contacting the most" should actually mean

Counting messages is the obvious approach and it is wrong in three specific
ways. It ranks newsletters above collaborators, it ranks the person who CCs
forty people above the person who writes to you alone, and it treats a
correspondence that ended in 2023 as equal to one that is live this week.

The implemented model corrects all three. For each interaction event *e*
between the user and counterparty *c*:

```
w(e) = ω(channel, direction) · d(audience) · exp(−λ · Δt)
```

- **ω** — channel and direction weight. Outbound acts cost the user effort and
  are stronger evidence of an intentional tie than inbound ones, which the
  counterparty can manufacture at no cost. A scheduled meeting (6.0) outranks
  an outbound call (5.0), an outbound email (3.0) and an inbound email (1.0).
- **d(n) = 1 / (1 + log₂ n)** — audience damping. A message to *n* people carries
  a fraction of the weight of a one-to-one message; a mail to 30 recipients is
  worth about 17% of a private one. Smooth decay rather than an arbitrary cutoff.
- **exp(−λΔt)**, λ = ln2 / *H* — exponential recency decay with a half-life *H*
  of 180 days by default.

Directional volumes *O* and *I* are then combined:

```
R = 2·√(O·I) / (O + I)              reciprocity  ∈ [0,1]
S = (O + I) · (0.25 + 0.75·R)       tie strength
```

*R* is the ratio of the geometric to the arithmetic mean of the two directional
volumes: 1.0 for a balanced exchange, 0.0 for a wholly one-sided one, smooth and
scale-free in between. It is what separates a colleague from a mailing list. The
0.25 floor stops a large one-sided relationship from collapsing to zero, since a
publisher you read every week is *something*, just not a contact.

A calendar event is credited to both directions at half weight each: a meeting
is inherently mutual, so it should strengthen the tie without distorting
reciprocity.

Two filters sit on top of the score rather than inside it, deliberately —
filters are auditable, score fudges are not:

- **Role and machine senders** are dropped by localpart and domain pattern
  (`no-reply@`, `notifications@`, `billing@`, ESP bounce domains, and so on).
- **One-shot bursts** are dropped unless a call or a meeting took place: a tie is
  evidenced by recurrence across distinct days, not by one busy afternoon.

Recency is expressed as decay, never as a cliff. A counterparty who went quiet
two years ago still appears, at roughly a fifteenth of the weight they carried
at the time — which is usually right, because that is exactly the person whose
photograph you have forgotten. `--since` is the control for the present-tense
question; narrowing it to 365 days removes the dormant.

The model is close to the tie-strength literature on communication-derived
social networks, where reciprocity and recency consistently outperform raw
volume. It is not novel, and it should not be; it should be legible.

### 2.4 Identity fusion

One person is an email address, four email addresses, two phone numbers and an
iMessage handle. The tool builds a union-find over identity keys and fuses the
keys listed on a single Contacts card. Phone numbers are keyed on their last
nine significant digits, which makes `+39 055 1234567`, `0039 055 1234567` and
`055 1234567` the same person without requiring a country-code library or
knowledge of the user's home region. This is a deliberate trade: collisions are
possible in principle, negligible at address-book scale, and documented.

### 2.5 If you do not use Mail.app

The Mail store will be empty for anyone on Spark, Outlook or a browser client,
and Calendar/Messages/Calls will still carry the signal. Additional adapters, in
descending order of effort: a Gmail Takeout mbox or an IMAP walk of the Sent
folder; Outlook's own local store; Spark's API. The extractor interface is a
generator yielding `Event` records, so an adapter is roughly sixty lines.

---

## 3. Stage B — resolving a person to a LinkedIn profile

This is an entity-resolution problem, and it is the part most likely to produce
silently wrong data. "Marco Bianchi, Prato" has homonyms. A confident-looking
wrong photograph in your address book is worse than no photograph.

The right approach is a **source ladder**, cheapest and most legitimate first,
stopping at the first confident answer. Most of the ladder never touches
LinkedIn:

1. **The address book itself.** `ZABCDSOCIALPROFILE` and `ZABCDURLADDRESS`
   already hold LinkedIn URLs for some contacts. Free, already yours,
   zero risk. `contact-rank` already surfaces these in the `linkedin_url`
   column — run it and see how many you have before building anything else.
2. **LinkedIn's own data export.** Settings → Data Privacy → *Get a copy of your
   data* → Connections yields a CSV of your first-degree connections with name,
   company, position and profile URL. First-party, sanctioned, no scraping, and
   for most people this single step resolves the majority of the queue. This is
   the highest-value item in the entire proposal and it costs one click.
3. **Email signature mining** over your own mailbox. Signatures routinely carry
   a LinkedIn URL, a title and a company. It is your own correspondence, so the
   processing basis is unproblematic, and the yield is high for professional
   contacts.
4. **Search, via a paid API with terms that permit it** (Brave, Bing) rather than
   by scraping a results page. Query on name plus the organisation inferred from
   the email domain. Treat results as candidates, never as answers.
5. **Ask the person.** A "share your card" link is the strongest consent posture
   available and takes one message.

Candidates are then scored — name similarity, company match against the email
domain, location, mutual connections — and anything below the threshold goes to
a human. Every stored record carries `matched_by` and `match_confidence`, so a
bad match can be found and undone later. **Default to human confirmation**; the
automated matcher will be wrong on common names often enough to matter, and the
cost of a wrong photograph is borne by the person in it.

---

## 4. Stage C — acquiring the image

### 4.1 Non-LinkedIn sources first

Before automating anything: contacts already carrying a photo in the address
book need nothing; Gravatar and Libravatar are opt-in services designed for
exactly this lookup and carry no risk at all; company team pages are usually
published for precisely this purpose. Run the queue through these first. What
remains after that is a much smaller set than the address book you started with,
which changes the risk calculus considerably.

### 4.2 Four postures for the remainder

| | Posture | Mechanism | ToS exposure | GDPR posture | Verdict |
|---|---|---|---|---|---|
| C1 | **Manual** | App opens the profile in the default browser; user saves the photo | None | Clean | Works, doesn't scale |
| C2 | **Assisted** | Embedded `WKWebView` on the user's own session; app navigates to one profile and **stops**; user confirms identity and clicks Save | Minimal — a bookmark queue with a save button | Clean, with a human accuracy check | Recommended; not chosen |
| C3 | **Rate-limited unattended** ✅ | Same, but the app advances and saves by itself | Real: §8.2 breach, account restriction | Defensible with a documented LIA and the §4.4 budgets | **Chosen** — see §4.5 |
| C4 | **Evasive** | UA spoofing, proxy rotation, CAPTCHA solving | Severe; changes the legal character of the act | Indefensible | **Out of scope. Will not be built.** |

C2 was the recommendation: it satisfies the original request almost exactly —
individual profiles, at human pace, through the web interface — while
remaining, in substance, a person browsing with a well-organised queue, and it
is *better data*, because a human catches the wrong-Marco-Bianchi case that no
matcher will.

### 4.3 Engineering notes for the embedded browser

- **Session.** A persistent `WKWebsiteDataStore` holding the user's own login.
  The application never sees or stores credentials; LinkedIn's login page runs
  in the web view and nowhere else.
- **Extraction.** Do not parse CSS class names — LinkedIn's markup churns
  constantly and class-based selectors break within weeks. Read
  `<meta property="og:image">` and the JSON-LD `<script type="application/ld+json">`
  block, both of which are far more stable.
- **Image URLs expire.** Avatars are served from `media.licdn.com` with signed,
  time-limited query parameters. Store the bytes, not the URL, and record the
  retrieval timestamp.
- **Absent photographs are a signal.** Many profiles have no public photo, or
  restrict it to connections. If it is not visible to you as an ordinary viewer,
  that is a visibility preference — treat it as a decision, not an obstacle.

### 4.4 Politeness engineering (binding under C3)

These are the parameters that make an unattended run defensible as "slow"
rather than merely slower. Under the decision in §4.5 they are hard-coded, not
settings:

- **Concurrency 1.** Never parallel, including image fetches.
- **Poisson-jittered intervals** around a 90 s mean, never a fixed cadence — a
  constant interval is the single easiest bot signature to detect, and a
  regular 60 s beat is more obviously automated than an irregular 40 s one.
- **Budgets:** ≤ 40 profiles per day, ≤ 15 per contiguous session, ≥ 2 h
  cool-down between sessions.
- **Working hours only,** in the user's local timezone. Nothing at 03:00.
- **Circuit breaker.** An HTTP 999, a `/checkpoint/` or `/authwall` redirect, an
  unexpected login page or any CAPTCHA halts the entire run until the user
  intervenes. Never retry a challenge — a challenge is the counterparty saying
  no, and retrying it is the step that turns rate-limiting into evasion.
- **Exponential backoff with full jitter** on any non-200; honour `Retry-After`.
- **Persistent, resumable queue** with per-profile state, so stopping is always
  safe and never loses work.

### 4.5 Decision: C3, with C2's safeguards kept where they are cheap

**C3 was chosen** by the decision owner, with the §4.4 budgets and circuit
breaker binding rather than configurable. Recording what that costs, so the
choice stays visible rather than implicit:

- **The exposure is authentication, not rate.** Slowing down does not reduce the
  contractual position; being logged in is what makes it §8.2 automated access.
  The practical remedy LinkedIn reaches for is account restriction, and the
  account is the user's own professional presence. §4.4 reduces the chance of
  being noticed; it does not change what is being done.
- **C4 remains out of scope and will not be built.** User-agent spoofing, proxy
  rotation and CAPTCHA solving are not "more of C3" — they are the step from
  rate-limited automation to evading a control, and design rule 4 is not
  relaxed by this decision.
- **Accuracy needs a different mechanism now.** Without a human on every save,
  Article 5(1)(d) is served by a confidence threshold instead: below it, a match
  is queued for review rather than written. See the revised rule 3 in §5.7.
- **C2 remains the first run.** The queue should be worked attended once before
  it is left alone, so the confidence threshold is calibrated against real
  matches rather than guessed.

---

## 5. Legal and regulatory analysis (EU)

*Not legal advice. This sets out the framework so that the design choices below
are traceable to specific obligations, and so that counsel can be asked a
narrow question rather than an open one.*

### 5.1 Does GDPR apply at all?

Almost certainly yes. Article 2(2)(c) exempts processing "in the course of a
purely personal or household activity", but the CJEU construes this narrowly —
*Lindqvist* (C-101/01) and *Ryneš* (C-212/13) both refused it where processing
touched the outside world. A tool built to "connect to the textile ecosystem" is
professional by its own description. **Assume full GDPR application and design
accordingly**; if it later turns out to be purely personal, nothing is lost.

The controller is the user or the studio. A profile photograph of an identified
person is personal data. Stage A is also processing — arguably more sensitive
than Stage C, since inferring relationship strength from communication metadata
is exactly the kind of profiling the Regulation is alert to. That it never
leaves the machine is a strong mitigation, not an exemption.

### 5.2 Are photographs special-category data?

**No — provided you never run face recognition on them.** Article 4(14) and
Recital 51 are explicit: photographs become biometric data only when processed
"through a specific technical means allowing the unique identification or
authentication" of a person. A stored image is an ordinary personal datum. Add
face matching, deduplication by face, or clustering, and Article 9 engages, the
lawful basis largely evaporates, and you are in the territory that produced
€20 m fines against Clearview AI from the French, Italian, Greek and UK
authorities in 2022.

This is also where the **AI Act** (Regulation (EU) 2024/1689) matters. Article
5(1)(e) — applicable since 2 February 2025 — prohibits AI systems that create or
expand facial-recognition databases through untargeted scraping of facial images
from the internet. A photograph cache for contacts you already correspond with
is neither untargeted nor a facial-recognition database. That remains true only
as long as no face processing is ever added, which is why it is a hard rule
below and not a preference.

### 5.3 Lawful basis

Article 6(1)(f), legitimate interests, is the realistic basis, and it requires a
documented three-part assessment — purpose, necessity, balancing — per EDPB
Guidelines 1/2024. The assessment is materially helped by two facts about this
design and materially harmed if either is given up:

- The data subjects are people with whom a **prior, evidenced interaction
  exists** in the local stores. This is a bounded, targeted set of business
  contacts, not a harvest of strangers. That distinction is the whole ballgame.
- The data collected is **one small image plus provenance**, nothing else.

Article 21(1) gives an absolute-in-practice right to object; erasure must
actually work, not merely be promised.

### 5.4 Transparency

Article 14 governs data not obtained from the data subject: they must be
informed within a reasonable period, at the latest one month, or at first
communication with them. The Article 14(5)(b) "disproportionate effort"
exemption is construed narrowly and is a poor fit here — these are people you
email. The cheap, correct answer is a short privacy notice at a stable URL,
linked from the email signature. Draft in `docs/legal/0001-gdpr-assessment.md`.

### 5.5 Contract, copyright, and technical reservations

Four distinct issues, often collapsed into one:

- **LinkedIn's User Agreement** (§8.2) prohibits scraping, copying profile data
  and automated access. Breach is a contract matter, with account restriction as
  the practical remedy and litigation as the occasional one. Note the shape of
  the US case law: *hiQ v. LinkedIn* held that scraping **public** pages is
  unlikely to violate the CFAA, but on remand LinkedIn won on **breach of
  contract**, and *Meta v. Bright Data* (N.D. Cal. 2024) turned on whether the
  scraper was **logged in**. The distinction is directly load-bearing here,
  because this design is logged in by construction. That is precisely where the
  contract bites hardest, and it is the exposure that the C3 decision (§4.5)
  accepts: a human clicking Save would not be "automated access" in the relevant
  sense, and an unattended run is.
- **Copyright.** A portrait is a protected work under Directive 2001/29/EC and
  the rights usually sit with the subject or their photographer, not with
  LinkedIn. The Article 5(2)(b) private-copying exception requires a natural
  person acting for private, non-commercial ends — which will not cover a studio
  CRM. Practical mitigation: internal display only, no redistribution, no
  publication, delete on request.
- **The DSM Directive's TDM exception** (2019/790 Art. 4) does not rescue this.
  It yields where rights have been reserved in a machine-readable manner, and
  robots.txt plus the User Agreement constitute exactly such a reservation.
- **robots.txt.** LinkedIn disallows crawling of `/in/` for general agents.
  Ignoring it is not itself unlawful in the EU, but it is evidence of
  unauthorised access, and in some member states circumventing an access control
  raises separate questions (in Germany, §202a StGB). Circumventing an access
  control is a different matter from ignoring robots.txt, which is why rule 4
  holds regardless of posture.

### 5.6 Accountability

Two housekeeping items that are cheap now and expensive retrofitted. An
Article 30 record of processing: the SME exemption in Article 30(5) does not
apply to processing that is other than occasional, and this is systematic. And a
DPIA: this is not on a mandatory list, but it meets at least two of the nine
WP248 criteria — *evaluation or scoring* and *matching or combining datasets* —
so one is advisable, and it is a page, not a project.

### 5.7 The design rules that follow

These are constraints on the implementation, not aspirations:

1. **No face recognition, matching, clustering or deduplication on stored
   images. Ever.** This single rule keeps Article 9 and AI Act Article 5(1)(e)
   out of scope.
2. **Enrich only people with an evidenced prior interaction** in the local
   stores. No speculative lookups, no expanding to second-degree contacts.
3. **Every match records its basis and confidence.** Matches below the
   confidence threshold are queued for human review and never written
   unattended; every saved record is reversible in one click. (Revised from
   "a human confirms every save" when C3 was chosen — §4.5.)
4. **Never evade a technical control.** No user-agent spoofing, no proxy
   rotation, no CAPTCHA solving. A challenge stops the run.
5. **Every stored record carries provenance, a retrieval timestamp, a retention
   TTL and a one-click erase** that actually deletes the bytes.
6. **Nothing leaves the machine** without an explicit, per-destination decision.

---

## 6. Architecture

```
                 ┌──────────────────────────────────────────┐
   local only    │  Stage A — contact graph                 │
   read-only     │  Mail · Messages · Calls · Calendar       │
                 │  Contacts · (CoreDuet, opt-in)            │
                 └────────────────┬─────────────────────────┘
                                  │  ranked people + identities
                                  ▼
                 ┌──────────────────────────────────────────┐
   no network    │  Stage B — profile resolution            │
   for 1–3       │  1 address book · 2 LinkedIn export ·     │
                 │  3 signatures · 4 search API · 5 ask      │
                 └────────────────┬─────────────────────────┘
                                  │  candidate URLs + confidence
                                  ▼
                 ┌──────────────────────────────────────────┐
   human in      │  Stage C — image acquisition             │
   the loop      │  contacts · Gravatar · site · WKWebView   │
                 └────────────────┬─────────────────────────┘
                                  ▼
                        SQLite store + image blobs
                   provenance · confidence · TTL · erase
```

The store is a single SQLite file under `~/Library/Application Support/`, with
images as files beside it. Every row records where it came from, when, with what
confidence, and when it expires. Rebuild-from-scratch must always be possible;
nothing in the store is authoritative except the user's own corrections.

Platform decisions that follow from section 2.2: Swift and SwiftUI, macOS 13+,
**non-sandboxed**, Developer ID–signed and notarised, distributed outside the
Mac App Store, updated through Sparkle. Stage A's queries are validated by
`tools/contact-rank` and should be ported to Swift essentially verbatim.

### Naming

If the family is to grow, textile terms carry the structure well: **Orditura**
(*warp* — the fixed threads: the contact graph, this tool), **Trama** (*weft* —
what is woven across it: enrichment), **Navetta** (*shuttle* — the carrier that
does the fetching). Offered, not imposed.

---

## 7. Recommendation

1. **Ship Stage A on its own first.** Run `tools/contact-rank` today, before any
   further code is written. It answers the original question, and the ranked
   list will very likely change your view of what the tool should do next.
2. **Do LinkedIn's data export before building any resolver.** One click; likely
   resolves most of the queue; entirely sanctioned.
3. **Exhaust the non-LinkedIn photo sources** — existing contact photos,
   Gravatar, company sites. Measure what is left. The residue is usually small.
4. **Stage C is C3**, per §4.5, with the §4.4 budgets and circuit breaker
   hard-coded rather than configurable, and the confidence threshold doing the
   accuracy work that a human would otherwise do.
5. **Work the first queue attended anyway**, to calibrate that threshold against
   real matches before anything runs unsupervised.
6. **Write the LIA and the privacy notice before the first fetch**, not after.
   Both are short. `docs/legal/0001-gdpr-assessment.md` has the drafts.

## 8. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | LinkedIn restricts or bans the user's account | **Medium — C3 accepted by the decision owner (§4.5)** | High — personal professional cost | §4.4 budgets; circuit breaker; no evasion (rule 4) |
| 2 | Wrong person's photograph stored | Medium under C3 | Medium — corrupts the address book | Confidence threshold; review queue below it; `match_confidence` recorded; reversible |
| 3 | macOS schema change breaks an extractor | High over a few releases | Low | Schema introspection; per-source degradation; `probe` |
| 4 | Full Disk Access refused or revoked | Medium | Medium — no ranking | Explicit onboarding; partial results from Contacts alone |
| 5 | Art. 14 notice never issued | Medium | Medium — regulatory | Notice URL in signature before first fetch |
| 6 | Scope creep into face processing | Low | **Severe** — Art. 9 + AI Act | Hard rule 1; no image-analysis dependency in the project |
| 7 | Ranked CSV leaks | Medium | Medium | Off by default; explicit `--out`; warning on write |
