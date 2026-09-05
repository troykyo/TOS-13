# GDPR assessment — contact graph and profile enrichment

**Status:** draft for completion by the controller. **Not legal advice.**
Companion to `docs/proposals/0001-contact-graph-and-profile-enrichment.md`,
section 5. The bracketed fields are the ones only you can fill in; everything
else is drafted.

---

## 1. Identification

| | |
|---|---|
| Controller | `[legal entity, address, VAT/registration number]` |
| Contact point | `[email address for data-subject requests]` |
| DPO | `[appointed / not appointed — Art. 37 is unlikely to require one here]` |
| Processors | None. All processing is local to the controller's Mac. |
| Third-country transfers | None by design. If a search API is used at Stage B, a name and an organisation are disclosed to that provider — record it here and check the provider's transfer mechanism. |

## 2. Processing operations

| Op | Purpose | Categories of data | Source | Basis |
|---|---|---|---|---|
| **A** Contact graph | Identify the professional contacts who matter most, to prioritise relationship management | Names, email addresses, phone numbers, communication metadata (timestamps, direction, audience size). **No message content.** | The controller's own Mac | Art. 6(1)(f) |
| **B** Profile resolution | Associate a known contact with their public professional profile | Name, organisation, public profile URL | Address book, LinkedIn data export, own correspondence signatures, optionally a search API | Art. 6(1)(f) |
| **C** Photograph | Recognise contacts in a professional address book | One profile photograph, provenance, retrieval timestamp | Existing contact card, Gravatar, employer website, or LinkedIn with human confirmation | Art. 6(1)(f) |

**Data subjects.** Business contacts of the controller with whom a prior,
evidenced interaction exists in the controller's own records. No speculative
lookups; no expansion to people not already corresponded with.

**Not special-category data.** The photographs are stored as ordinary images.
No face recognition, matching, clustering or deduplication is performed on
them, so Art. 4(14) and Recital 51 are not engaged and Art. 9 does not apply.
This is enforced as a hard design rule, not a policy statement: no
image-analysis dependency exists in the project. It is also what keeps
AI Act Art. 5(1)(e) out of scope.

## 3. Legitimate interests assessment (Art. 6(1)(f), EDPB Guidelines 1/2024)

### 3.1 Purpose test — is the interest legitimate?

Maintaining an accurate professional address book, and knowing which
relationships are active, is a routine and lawful business interest. Recital 47
recognises processing for ordinary business purposes where the data subject can
reasonably expect it. The contacts are people the controller already corresponds
with in a professional capacity.

### 3.2 Necessity test — is the processing necessary, and is there a less intrusive route?

Operation A is necessary: no alternative source describes the controller's own
communication history, and it is metadata-only — content is never read. Ranking
by interaction is precisely what makes the enrichment queue *targeted* rather
than a bulk harvest, so it also serves the balancing test below.

Operations B and C are necessary in the weaker sense that recognising a contact
requires the contact's likeness. The necessity test is met by the source ladder:
the LinkedIn route is used only for the residue that the address book, the
first-party LinkedIn export, Gravatar and employer websites have not already
resolved. Collection is limited to one image; no other profile field is
retained.

### 3.3 Balancing test — do the data subjects' rights override?

| Factor | Assessment |
|---|---|
| Nature of the data | Business-context identifiers and a professional photograph the subject published for professional identification. No special categories, no financial or content data. |
| Reasonable expectations | High for A (they emailed the controller) and for C where the photograph is publicly visible on a professional network. Lower for the fact that a photograph is *retained locally*, which is what Art. 14 notice addresses. |
| Relationship | Existing professional relationship in every case. This is the decisive factor and it is preserved only while design rule 2 holds. |
| Scale | Bounded: hundreds of known contacts, not an open harvest. |
| Intrusiveness | Low. Local storage, single-user access, no profiling of the subject's behaviour, no automated decision-making about them, no disclosure. |
| Countervailing risk | The graph reveals the *controller's own* social pattern more than any subject's. It never leaves the machine. |
| Safeguards | Sections 4–6 below. |

**Conclusion:** the interest is not overridden, **conditional on** the six design
rules in proposal §5.7 being maintained. Removing rule 1 (no face processing) or
rule 2 (evidenced prior interaction only) invalidates this assessment and it
must be redone.

## 4. Transparency (Art. 14)

Data at stages B and C are not obtained from the data subject, so Art. 14
applies: inform within a reasonable period, at the latest one month, or at first
communication with them (Art. 14(3)(a)–(b)). The Art. 14(5)(b) disproportionate-
effort exemption is construed narrowly and is not relied on — these are people
the controller emails, so notice is cheap.

**Mechanism:** a privacy notice at a stable URL, linked from the controller's
email signature, so that first communication carries it.

> ### Draft notice
>
> **How `[controller]` handles your contact details**
>
> We keep a professional address book. It contains your name, the contact
> details you have used with us, and — where you have published one on a
> professional network or your employer's website — your profile photograph, so
> that we can recognise you. We also record how often we have been in contact,
> derived from our own email, calendar and call records. We never read the
> content of messages for this purpose.
>
> We do this on the basis of our legitimate interest in maintaining an accurate
> record of our professional relationships (Article 6(1)(f) GDPR). We do not use
> facial recognition of any kind, we do not share this information with anyone,
> and it is stored only on our own computers within the EU.
>
> You can ask us at any time what we hold about you, ask us to correct or delete
> it, or object to this processing altogether — including asking us to remove
> your photograph while keeping your contact details. Write to `[address]` and
> we will act on it. You may also complain to your national supervisory
> authority `[e.g. the Garante per la protezione dei dati personali]`.

## 5. Data-subject rights — how each is actually served

| Right | Implementation |
|---|---|
| Access (Art. 15) | `export --subject <address>` produces every stored row and image for one person |
| Rectification (Art. 16) | Any field is user-editable; corrections outrank derived values and survive a rebuild |
| Erasure (Art. 17) | `forget <address>` deletes rows and image bytes, and writes a tombstone so re-enrichment does not resurrect the record |
| Objection (Art. 21) | Same tombstone. An objection to the photograph alone leaves contact details intact |
| Portability (Art. 20) | Not engaged — basis is 6(1)(f), not consent or contract. JSON export exists anyway |

The tombstone matters: without it, the next run re-adds the person a user asked
to be forgotten, which converts a satisfied request into a repeated violation.

## 6. Security and retention

- Local storage only; no server component, no telemetry, no crash reporting that
  could carry personal data.
- FileVault assumed on the host. `[confirm]`
- Retention: photographs **24 months** from retrieval, then re-verify or delete.
  Contact-graph metadata **36 months** rolling, matching the tool's default
  window. Records for contacts with no interaction inside the window are pruned.
- The ranked CSV/JSON export is off by default and warns on write. It is
  personal data in the clear and should not be committed, mailed or synced to a
  third-party drive.
- No image ever leaves the machine; no image is published or redistributed.
  This also keeps the copyright exposure in proposal §5.5 at its minimum.

## 7. Accountability

- **Art. 30 record of processing:** required. The Art. 30(5) SME exemption does
  not cover processing that is other than occasional, and this is systematic.
  Section 2 above is drafted to be pasted into the register.
- **DPIA (Art. 35):** not mandatory on the face of it, but two of the nine WP248
  criteria are met — *evaluation or scoring* and *matching or combining
  datasets*. A short DPIA is advisable and this document supplies most of it.
- **Review:** re-run this assessment if any design rule changes, if a new data
  source is added, if data begins to leave the machine, or annually.

## 8. Open questions for counsel

1. Is the controller the studio or the individual? It affects Art. 30 and the
   household-exemption argument, which we have declined to rely on.
2. Does retaining a LinkedIn profile photograph for internal recognition raise a
   copyright issue counsel would want addressed beyond "internal use, no
   redistribution, delete on request"?
3. Is the Art. 14 notice-by-signature mechanism acceptable, or is individual
   notification preferred for the first backfill?
