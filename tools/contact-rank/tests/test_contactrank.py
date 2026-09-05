#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for contactrank.

The macOS stores this tool reads cannot exist on CI, so every extractor is
exercised against a synthetic SQLite fixture built to the same column shape as
the real store. That verifies the SQL, the epoch handling and the direction
logic; it cannot verify that Apple has not renamed a column in the next macOS
release, which is why `contactrank probe` introspects the live schema and every
extractor degrades to a SourceError instead of a crash.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contactrank as cr  # noqa: E402


NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0


def days_ago(n: float) -> float:
    return NOW - n * DAY


def apple(ts: float) -> float:
    return ts - cr.APPLE_EPOCH


def apple_ns(ts: float) -> int:
    return int((ts - cr.APPLE_EPOCH) * 1e9)


class TmpDB:
    """Create a throwaway SQLite file and return its path."""

    def __init__(self, name: str, schema: str, rows=()):
        self.dir = tempfile.mkdtemp(prefix="crtest-")
        self.path = Path(self.dir) / name
        conn = sqlite3.connect(str(self.path))
        conn.executescript(schema)
        for sql, params in rows:
            conn.executemany(sql, params)
        conn.commit()
        conn.close()

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Identity normalisation
# ---------------------------------------------------------------------------

class TestNormalisation(unittest.TestCase):
    def test_email_forms(self):
        self.assertEqual(cr.norm_email("A.Rossi@Lanificio.IT"), "mailto:a.rossi@lanificio.it")
        self.assertEqual(cr.norm_email("  <a@b.co>  "), "mailto:a@b.co")
        self.assertEqual(cr.norm_email('"Anna Rossi" <anna@b.co>'), "mailto:anna@b.co")
        self.assertIsNone(cr.norm_email("not-an-address"))
        self.assertIsNone(cr.norm_email(""))
        self.assertIsNone(cr.norm_email(None))

    def test_phone_last_nine_digits_are_stable(self):
        forms = ["+39 055 123 4567", "0039 055 1234567", "055 1234567", "39-055-1234567"]
        keys = {cr.norm_phone(f) for f in forms}
        self.assertEqual(len(keys), 1, keys)
        self.assertEqual(keys.pop(), "tel:551234567")

    def test_phone_rejects_short_input(self):
        self.assertIsNone(cr.norm_phone("123"))
        self.assertIsNone(cr.norm_phone(""))

    def test_handle_dispatches_on_at_sign(self):
        self.assertEqual(cr.norm_handle("x@y.zz"), "mailto:x@y.zz")
        self.assertEqual(cr.norm_handle(b"+390551234567"), "tel:551234567")

    def test_role_address_detection(self):
        for bad in ["mailto:no-reply@linkedin.com", "mailto:notifications@github.com",
                    "mailto:billing@stripe.com", "mailto:newsletter@vogue.it",
                    "mailto:bounce+123@sendgrid.net", "mailto:info@studio.it"]:
            self.assertTrue(cr.is_role_address(bad), bad)
        for good in ["mailto:anna.rossi@lanificio.it", "mailto:m.bianchi@politecnico.it",
                     "tel:551234567"]:
            self.assertFalse(cr.is_role_address(good), good)


# ---------------------------------------------------------------------------
# Scoring model
# ---------------------------------------------------------------------------

class TestScoringModel(unittest.TestCase):
    def test_audience_damping(self):
        self.assertAlmostEqual(cr.audience_damping(1), 1.0)
        self.assertAlmostEqual(cr.audience_damping(2), 0.5)
        self.assertLess(cr.audience_damping(50), cr.audience_damping(10))
        self.assertGreater(cr.audience_damping(50), 0.0)

    def test_reciprocity_bounds(self):
        p = cr.Person(root="x")
        p.out_score, p.in_score = 10.0, 10.0
        self.assertAlmostEqual(p.reciprocity, 1.0)
        p.out_score, p.in_score = 10.0, 0.0
        self.assertAlmostEqual(p.reciprocity, 0.0)
        p.out_score, p.in_score = 9.0, 1.0
        self.assertAlmostEqual(p.reciprocity, 0.6)

    def test_one_sided_tie_keeps_only_the_floor(self):
        p = cr.Person(root="x")
        p.out_score, p.in_score = 0.0, 100.0
        self.assertAlmostEqual(p.score, 100.0 * cr.RECIPROCITY_FLOOR)

    def test_reciprocal_beats_one_sided_at_equal_volume(self):
        bal = cr.Person(root="a"); bal.out_score = bal.in_score = 50.0
        one = cr.Person(root="b"); one.in_score = 100.0
        self.assertAlmostEqual(bal.volume, one.volume)
        self.assertGreater(bal.score, one.score)

    def test_recency_decay_at_the_half_life(self):
        old = cr.Event("mailto:a@x.it", "mail", "out", days_ago(180))
        new = cr.Event("mailto:b@x.it", "mail", "out", days_ago(0))
        people = {p.root: p for p in cr.aggregate([old, new], [], NOW, half_life_days=180.0)}
        self.assertAlmostEqual(
            people["mailto:a@x.it"].out_score / people["mailto:b@x.it"].out_score, 0.5, places=6)

    def test_identities_are_fused_through_the_address_book(self):
        card = cr.Card(uid="U1", first="Anna", last="Rossi",
                       emails=["mailto:anna@lanificio.it"], phones=["tel:551234567"])
        events = [
            cr.Event("mailto:anna@lanificio.it", "mail", "out", days_ago(1)),
            cr.Event("tel:551234567", "imessage", "in", days_ago(2)),
        ]
        people = cr.aggregate(events, [card], NOW)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].best_display, "Anna Rossi")
        self.assertEqual(people[0].channels, {"mail", "imessage"})

    def test_meetings_are_counted_as_mutual(self):
        people = cr.aggregate([cr.Event("mailto:a@x.it", "calendar", "meet", days_ago(1), 3)],
                              [], NOW)
        p = people[0]
        self.assertAlmostEqual(p.out_score, p.in_score)
        self.assertAlmostEqual(p.reciprocity, 1.0)
        self.assertEqual(p.n_meet, 1)

    def test_role_addresses_are_dropped_by_default(self):
        events = [cr.Event("mailto:no-reply@linkedin.com", "mail", "in", days_ago(i))
                  for i in range(10)]
        self.assertEqual(cr.aggregate(events, [], NOW), [])
        self.assertEqual(len(cr.aggregate(events, [], NOW, drop_role_addresses=False)), 1)

    def test_min_active_days_filter_spares_calls_and_meetings(self):
        burst = cr.Person(root="a"); burst.days = {"2026-08-30"}
        called = cr.Person(root="b"); called.days = {"2026-08-30"}; called.n_call = 1
        met = cr.Person(root="c"); met.days = {"2026-08-30"}; met.n_meet = 1
        kept = cr.filter_people([burst, called, met], min_active_days=3)
        self.assertEqual([p.root for p in kept], ["b", "c"])


# ---------------------------------------------------------------------------
# Extractors, against schema-shaped fixtures
# ---------------------------------------------------------------------------

MAIL_SCHEMA = """
CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, sender INTEGER, subject INTEGER,
                       date_sent INTEGER, date_received INTEGER, mailbox INTEGER,
                       deleted INTEGER DEFAULT 0, automated_type INTEGER DEFAULT 0);
CREATE TABLE recipients (ROWID INTEGER PRIMARY KEY, message INTEGER, type INTEGER,
                         address INTEGER, position INTEGER);
"""


def mail_fixture():
    addrs = [
        (1, "me@studio.it", "Me"),
        (2, "anna@lanificio.it", "Anna Rossi"),
        (3, "marco@filatura.it", "Marco Bianchi"),
        (4, "no-reply@platform.com", "Platform"),
    ]
    boxes = [
        (1, "imap://me%40studio.it@imap.example.com/INBOX"),
        (2, "imap://me%40studio.it@imap.example.com/Sent%20Messages"),
    ]
    msgs = [
        # outgoing, one-to-one, recent
        (10, 1, None, int(days_ago(2)), int(days_ago(2)), 2, 0, 0),
        # incoming from Anna
        (11, 2, None, int(days_ago(2)), int(days_ago(2)), 1, 0, 0),
        # outgoing broadcast to Anna + Marco + one more
        (12, 1, None, int(days_ago(5)), int(days_ago(5)), 2, 0, 0),
        # incoming bulk from a no-reply sender
        (13, 4, None, int(days_ago(1)), int(days_ago(1)), 1, 0, 0),
        # soft-deleted, must be ignored
        (14, 2, None, int(days_ago(3)), int(days_ago(3)), 1, 1, 0),
        # flagged automated by Mail, must be ignored
        (15, 3, None, int(days_ago(3)), int(days_ago(3)), 1, 0, 1),
    ]
    rcpts = [
        (1, 10, 0, 2, 0),
        (2, 11, 0, 1, 0),
        (3, 12, 0, 2, 0), (4, 12, 0, 3, 1), (5, 12, 1, 4, 2),
        (6, 13, 0, 1, 0),
        (7, 14, 0, 1, 0),
        (8, 15, 0, 1, 0),
    ]
    return TmpDB("Envelope Index", MAIL_SCHEMA, [
        ("INSERT INTO addresses VALUES (?,?,?)", addrs),
        ("INSERT INTO mailboxes VALUES (?,?)", boxes),
        ("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", msgs),
        ("INSERT INTO recipients VALUES (?,?,?,?,?)", rcpts),
    ])


class TestMailExtractor(unittest.TestCase):
    def setUp(self):
        self.fx = mail_fixture()
        self.path = self.fx.__enter__()
        self.events = list(cr.extract_mail(self.path, days_ago(365), set()))

    def tearDown(self):
        self.fx.__exit__(None, None, None)

    def test_own_address_detected_from_mailbox_url(self):
        with cr.ReadOnlyDB(self.path) as conn:
            self.assertIn("mailto:me@studio.it", cr.own_addresses_from_mail(conn))

    def test_directions(self):
        out = {e.key for e in self.events if e.direction == "out"}
        inc = {e.key for e in self.events if e.direction == "in"}
        self.assertEqual(out, {"mailto:anna@lanificio.it", "mailto:marco@filatura.it",
                               "mailto:no-reply@platform.com"})
        self.assertIn("mailto:anna@lanificio.it", inc)

    def test_own_address_is_never_a_counterparty(self):
        self.assertNotIn("mailto:me@studio.it", {e.key for e in self.events})

    def test_deleted_and_automated_messages_are_excluded(self):
        # message 14 (deleted) and 15 (automated) are the only sources of a
        # second incoming event from Anna and any event from Marco inbound.
        anna_in = [e for e in self.events
                   if e.key == "mailto:anna@lanificio.it" and e.direction == "in"]
        self.assertEqual(len(anna_in), 1)
        self.assertEqual([e for e in self.events
                          if e.key == "mailto:marco@filatura.it" and e.direction == "in"], [])

    def test_audience_size_is_recorded(self):
        broadcast = [e for e in self.events
                     if e.direction == "out" and e.key == "mailto:marco@filatura.it"]
        self.assertEqual(len(broadcast), 1)
        self.assertEqual(broadcast[0].audience, 3)
        one_to_one = [e for e in self.events
                      if e.direction == "out" and e.key == "mailto:anna@lanificio.it"
                      and e.audience == 1]
        self.assertEqual(len(one_to_one), 1)

    def test_incoming_message_yields_exactly_one_event(self):
        bulk = [e for e in self.events if e.key == "mailto:no-reply@platform.com"
                and e.direction == "in"]
        self.assertEqual(len(bulk), 1)

    def test_display_names_are_carried(self):
        self.assertIn("Anna Rossi", {e.display for e in self.events})

    def test_window_is_respected(self):
        recent = list(cr.extract_mail(self.path, days_ago(3), set()))
        self.assertTrue(all(e.ts >= days_ago(3) for e in recent))
        self.assertLess(len(recent), len(self.events))


IMESSAGE_SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, style INTEGER);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, handle_id INTEGER, date INTEGER,
                      is_from_me INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
"""


class TestIMessageExtractor(unittest.TestCase):
    def setUp(self):
        self.fx = TmpDB("chat.db", IMESSAGE_SCHEMA, [
            ("INSERT INTO handle VALUES (?,?,?)", [
                (1, "+390551234567", "iMessage"),
                (2, "marco@filatura.it", "iMessage"),
            ]),
            ("INSERT INTO chat VALUES (?,?,?)", [(1, "+390551234567", 45), (2, "grp", 43)]),
            ("INSERT INTO chat_handle_join VALUES (?,?)", [(1, 1), (2, 1), (2, 2)]),
            ("INSERT INTO message VALUES (?,?,?,?)", [
                (100, 1, apple_ns(days_ago(1)), 0),      # from Anna, 1:1
                (101, 0, apple_ns(days_ago(1)), 1),      # from me, 1:1
                (102, 2, apple_ns(days_ago(4)), 0),      # from Marco, group
                (103, 0, apple_ns(days_ago(4)), 1),      # from me, group
                (104, 1, apple(days_ago(900)), 0),       # legacy seconds encoding, outside window
            ]),
            ("INSERT INTO chat_message_join VALUES (?,?)", [
                (1, 100), (1, 101), (2, 102), (2, 103), (1, 104)]),
        ])
        self.path = self.fx.__enter__()
        self.events = list(cr.extract_imessage(self.path, days_ago(365)))

    def tearDown(self):
        self.fx.__exit__(None, None, None)

    def test_nanosecond_epoch_is_decoded(self):
        ts = [e.ts for e in self.events if e.direction == "in"][0]
        self.assertAlmostEqual(ts, days_ago(1), delta=DAY)

    def test_out_of_window_legacy_row_is_dropped(self):
        self.assertTrue(all(e.ts >= days_ago(365) for e in self.events))

    def test_outgoing_group_message_reaches_every_participant(self):
        grp_out = {e.key for e in self.events if e.direction == "out" and e.audience == 2}
        self.assertEqual(grp_out, {"tel:551234567", "mailto:marco@filatura.it"})

    def test_one_to_one_audience_is_one(self):
        solo = [e for e in self.events if e.key == "tel:551234567" and e.audience == 1]
        self.assertTrue(solo)


CALL_SCHEMA = """
CREATE TABLE ZCALLRECORD (Z_PK INTEGER PRIMARY KEY, ZADDRESS BLOB, ZDATE REAL,
                          ZORIGINATED INTEGER, ZDURATION REAL, ZNAME TEXT);
"""


class TestCallExtractor(unittest.TestCase):
    def test_blob_addresses_and_direction(self):
        with TmpDB("CallHistory.storedata", CALL_SCHEMA, [
            ("INSERT INTO ZCALLRECORD VALUES (?,?,?,?,?,?)", [
                (1, b"+390551234567", apple(days_ago(3)), 1, 240.0, "Anna Rossi"),
                (2, "+390557654321", apple(days_ago(9)), 0, 60.0, None),
                (3, b"+390551234567", apple(days_ago(900)), 1, 10.0, None),
            ]),
        ]) as path:
            events = list(cr.extract_call(path, days_ago(365)))
        self.assertEqual(len(events), 2)
        by_key = {e.key: e for e in events}
        self.assertEqual(by_key["tel:551234567"].direction, "out")
        self.assertEqual(by_key["tel:557654321"].direction, "in")
        self.assertEqual(by_key["tel:551234567"].display, "Anna Rossi")


CALENDAR_SCHEMA = """
CREATE TABLE CalendarItem (ROWID INTEGER PRIMARY KEY, summary TEXT, start_date REAL);
CREATE TABLE Participant (ROWID INTEGER PRIMARY KEY, owner_id INTEGER, entity_type INTEGER,
                          email TEXT, phone_number TEXT, display_name TEXT, is_self INTEGER);
"""


class TestCalendarExtractor(unittest.TestCase):
    def _build(self):
        parts = [
            (1, 1, 3, "me@studio.it", None, "Me", 1),
            (2, 1, 3, "anna@lanificio.it", None, "Anna Rossi", 0),
            (3, 1, 3, "marco@filatura.it", None, "Marco Bianchi", 0),
        ]
        # a 40-person all-hands: above the attendee cap, must be ignored
        parts += [(10 + i, 2, 3, "p%d@confer.it" % i, None, "P%d" % i, 0) for i in range(40)]
        return TmpDB("Calendar.sqlitedb", CALENDAR_SCHEMA, [
            ("INSERT INTO CalendarItem VALUES (?,?,?)", [
                (1, "Campionatura AW26", apple(days_ago(6))),
                (2, "All hands", apple(days_ago(7))),
            ]),
            ("INSERT INTO Participant VALUES (?,?,?,?,?,?,?)", parts),
        ])

    def test_self_excluded_and_large_meetings_capped(self):
        with self._build() as path:
            events = list(cr.extract_calendar(path, days_ago(365)))
        keys = {e.key for e in events}
        self.assertEqual(keys, {"mailto:anna@lanificio.it", "mailto:marco@filatura.it"})
        self.assertTrue(all(e.direction == "meet" and e.audience == 2 for e in events))


ADDRESSBOOK_SCHEMA = """
CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZUNIQUEID TEXT, ZFIRSTNAME TEXT,
                          ZLASTNAME TEXT, ZORGANIZATION TEXT, ZJOBTITLE TEXT);
CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);
CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);
CREATE TABLE ZABCDSOCIALPROFILE (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZSERVICE TEXT,
                                 ZUSERNAME TEXT, ZURL TEXT);
CREATE TABLE ZABCDURLADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZURL TEXT);
"""


class TestContactsExtractor(unittest.TestCase):
    def test_cards_emails_phones_and_linkedin(self):
        with TmpDB("AddressBook-v22.abcddb", ADDRESSBOOK_SCHEMA, [
            ("INSERT INTO ZABCDRECORD VALUES (?,?,?,?,?,?)", [
                (1, "UID-1:ABPerson", "Anna", "Rossi", "Lanificio Rossi", "Direttrice creativa"),
                (2, "UID-2:ABPerson", "Marco", "Bianchi", "Filatura Bianchi", None),
            ]),
            ("INSERT INTO ZABCDEMAILADDRESS VALUES (?,?,?)", [
                (1, 1, "Anna@Lanificio.IT"), (2, 2, "marco@filatura.it")]),
            ("INSERT INTO ZABCDPHONENUMBER VALUES (?,?,?)", [(1, 1, "+39 055 1234567")]),
            ("INSERT INTO ZABCDSOCIALPROFILE VALUES (?,?,?,?,?)", [
                (1, 1, "LinkedIn", "anna-rossi", "https://www.linkedin.com/in/anna-rossi")]),
            ("INSERT INTO ZABCDURLADDRESS VALUES (?,?,?)", [
                (1, 2, "https://it.linkedin.com/in/marco-bianchi")]),
        ]) as path:
            cards = {c.uid: c for c in cr.extract_cards([path])}
        anna = cards["UID-1:ABPerson"]
        self.assertEqual(anna.name, "Anna Rossi")
        self.assertEqual(anna.org, "Lanificio Rossi")
        self.assertEqual(anna.emails, ["mailto:anna@lanificio.it"])
        self.assertEqual(anna.phones, ["tel:551234567"])
        self.assertEqual(anna.linkedin, "https://www.linkedin.com/in/anna-rossi")
        # a LinkedIn URL filed as a plain web address is picked up too
        self.assertEqual(cards["UID-2:ABPerson"].linkedin,
                         "https://it.linkedin.com/in/marco-bianchi")


COREDUET_SCHEMA = """
CREATE TABLE ZINTERACTIONS (Z_PK INTEGER PRIMARY KEY, ZDIRECTION INTEGER,
                            ZSTARTDATE REAL, ZBUNDLEID TEXT);
CREATE TABLE ZCONTACTS (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZDISPLAYNAME TEXT);
CREATE TABLE Z_1INTERACTIONS (Z_1INTERACTIONS INTEGER, Z_3CONTACTS INTEGER);
"""


class TestCoreDuetExtractor(unittest.TestCase):
    def test_join_table_is_discovered(self):
        with TmpDB("interactionC.db", COREDUET_SCHEMA, [
            ("INSERT INTO ZINTERACTIONS VALUES (?,?,?,?)", [
                (1, 1, apple(days_ago(2)), "com.apple.mail"),
                (2, 0, apple(days_ago(3)), "com.apple.MobileSMS"),
            ]),
            ("INSERT INTO ZCONTACTS VALUES (?,?,?)", [
                (1, "anna@lanificio.it", "Anna Rossi")]),
            ("INSERT INTO Z_1INTERACTIONS VALUES (?,?)", [(1, 1), (2, 1)]),
        ]) as path:
            events = list(cr.extract_coreduet(path, days_ago(365)))
        self.assertEqual(len(events), 2)
        self.assertEqual({e.direction for e in events}, {"out", "in"})
        self.assertTrue(all(e.key == "mailto:anna@lanificio.it" for e in events))


class TestSchemaDegradation(unittest.TestCase):
    def test_unknown_schema_raises_sourceerror_not_a_crash(self):
        with TmpDB("Envelope Index", "CREATE TABLE nonsense (a INTEGER);") as path:
            with self.assertRaises(cr.SourceError):
                list(cr.extract_mail(path, 0, set()))
            with self.assertRaises(cr.SourceError):
                list(cr.extract_imessage(path, 0))
            with self.assertRaises(cr.SourceError):
                list(cr.extract_calendar(path, 0))

    def test_missing_file_raises_sourceerror(self):
        with self.assertRaises(cr.SourceError):
            list(cr.extract_call(Path("/nonexistent/CallHistory.storedata"), 0))

    def test_reader_does_not_modify_the_source(self):
        with TmpDB("Envelope Index", MAIL_SCHEMA) as path:
            before = path.stat().st_mtime_ns, path.stat().st_size
            with cr.ReadOnlyDB(path) as conn:
                conn.execute("SELECT count(*) FROM messages").fetchone()
            self.assertEqual(before, (path.stat().st_mtime_ns, path.stat().st_size))


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestEndToEnd(unittest.TestCase):
    def _corpus(self):
        """
        Four counterparties, each of which dominates on one naive metric, so
        that only the full model orders them the way a person would:

          Anna       -- fewest messages, but reciprocal, one-to-one, plus a meeting
          Bruno      -- many outbound messages, always to 30 people, never replies
          Newsletter -- the highest raw message count, entirely inbound, no-reply
          Ghost      -- genuinely reciprocal, but two years stale
        """
        events = []
        for i in range(12):
            events.append(cr.Event("mailto:anna@lanificio.it", "mail", "out", days_ago(i * 5)))
            events.append(cr.Event("mailto:anna@lanificio.it", "mail", "in", days_ago(i * 5 + 1)))
        events.append(cr.Event("mailto:anna@lanificio.it", "calendar", "meet", days_ago(9), 3))
        for i in range(40):
            events.append(cr.Event("mailto:bruno@consorzio.it", "mail", "out",
                                   days_ago(i * 3), audience=30))
        for i in range(80):
            events.append(cr.Event("mailto:no-reply@textilenews.com", "mail", "in", days_ago(i)))
        for i in range(30):
            events.append(cr.Event("mailto:ghost@old.it", "mail", "out", days_ago(700 + i)))
            events.append(cr.Event("mailto:ghost@old.it", "mail", "in", days_ago(700 + i)))
        return events

    def _ranked(self, since_days=None):
        events = self._corpus()
        if since_days is not None:
            floor = days_ago(since_days)
            events = [e for e in events if e.ts >= floor]
        return cr.filter_people(cr.aggregate(events, [], NOW), min_active_days=3)

    def test_reciprocal_correspondent_ranks_first(self):
        ranked = self._ranked()
        self.assertEqual(ranked[0].root, "mailto:anna@lanificio.it")

    def test_bulk_sender_is_excluded_entirely(self):
        self.assertNotIn("mailto:no-reply@textilenews.com",
                         [p.root for p in self._ranked()])

    def test_broadcaster_loses_to_a_correspondent_with_a_third_the_traffic(self):
        by = {p.root: p for p in self._ranked()}
        anna, bruno = by["mailto:anna@lanificio.it"], by["mailto:bruno@consorzio.it"]
        self.assertGreater(bruno.n_out, anna.n_out)          # more raw messages
        self.assertGreater(anna.score, 2 * bruno.score)      # far weaker tie all the same
        self.assertAlmostEqual(bruno.reciprocity, 0.0)

    def test_stale_reciprocal_tie_survives_the_default_window_but_is_heavily_decayed(self):
        """
        A three-year default window is deliberately generous: an important
        counterparty who went quiet is still a counterparty, and the enrichment
        queue should be allowed to see them. Recency is expressed as decay, not
        as a cliff -- so a two-year-old exchange is still ranked, at roughly a
        fifteenth of the weight it carried when it happened.
        """
        by = {p.root: p for p in self._ranked()}
        ghost = by["mailto:ghost@old.it"]
        fresh = cr.aggregate(
            [cr.Event(e.key, e.channel, e.direction, e.ts + 700 * DAY, e.audience)
             for e in self._corpus() if e.key == "mailto:ghost@old.it"], [], NOW)[0]
        self.assertLess(ghost.score, fresh.score / 10)
        self.assertGreater(ghost.reciprocity, 0.8)

    def test_narrowing_the_window_drops_dormant_ties(self):
        """--since is the present-tense control: it is what removes the stale."""
        roots = [p.root for p in self._ranked(since_days=365)]
        self.assertNotIn("mailto:ghost@old.it", roots)
        self.assertEqual(roots[0], "mailto:anna@lanificio.it")

    def test_person_row_is_serialisable_and_complete(self):
        card = cr.Card(uid="U1", first="Anna", last="Rossi", org="Lanificio Rossi",
                       title="Direttrice creativa", emails=["mailto:anna@lanificio.it"],
                       linkedin="https://www.linkedin.com/in/anna-rossi")
        people = cr.aggregate([cr.Event("mailto:anna@lanificio.it", "mail", "out", days_ago(1)),
                               cr.Event("mailto:anna@lanificio.it", "mail", "in", days_ago(1))],
                              [card], NOW)
        row = cr.person_row(1, people[0])
        self.assertEqual(sorted(row.keys()), sorted(cr.COLUMNS))
        self.assertEqual(row["linkedin_url"], "https://www.linkedin.com/in/anna-rossi")
        self.assertTrue(row["in_contacts"])
        import json
        json.dumps(row)

    def test_cli_probe_runs_anywhere(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cr.main(["probe"])
        self.assertEqual(rc, 0)
        self.assertIn("contactrank probe", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
