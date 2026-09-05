#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-end CLI test against a synthetic home directory laid out exactly like a
Mac's, so that `probe` and `rank` are exercised through the same discovery,
aggregation, printing and CSV paths a real run takes.
"""

from __future__ import annotations

import contextlib
import csv
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import contactrank as cr  # noqa: E402
from test_contactrank import (  # noqa: E402
    ADDRESSBOOK_SCHEMA, CALENDAR_SCHEMA, CALL_SCHEMA, IMESSAGE_SCHEMA,
    MAIL_SCHEMA, apple, apple_ns, days_ago,
)


def write_db(path: Path, schema: str, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    for sql, params in rows:
        conn.executemany(sql, params)
    conn.commit()
    conn.close()


class TestCLIAgainstASyntheticHome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="crhome-"))
        L = cls.home / "Library"

        write_db(L / "Mail/V10/MailData/Envelope Index", MAIL_SCHEMA, [
            ("INSERT INTO addresses VALUES (?,?,?)", [
                (1, "me@studio.it", "Me"),
                (2, "anna@lanificio.it", "Anna Rossi"),
                (3, "marco@filatura.it", "Marco Bianchi"),
                (4, "newsletter@textilenews.com", "Textile News"),
            ]),
            ("INSERT INTO mailboxes VALUES (?,?)", [
                (1, "imap://me%40studio.it@imap.example.com/INBOX"),
                (2, "imap://me%40studio.it@imap.example.com/Sent%20Messages"),
            ]),
            ("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", (
                [(100 + i, 1, None, int(days_ago(i * 4)), int(days_ago(i * 4)), 2, 0, 0)
                 for i in range(8)] +
                [(200 + i, 2, None, int(days_ago(i * 4 + 1)), int(days_ago(i * 4 + 1)), 1, 0, 0)
                 for i in range(8)] +
                [(300 + i, 4, None, int(days_ago(i)), int(days_ago(i)), 1, 0, 0)
                 for i in range(30)]
            )),
            ("INSERT INTO recipients VALUES (?,?,?,?,?)", (
                [(1000 + i, 100 + i, 0, 2, 0) for i in range(8)] +
                [(2000 + i, 200 + i, 0, 1, 0) for i in range(8)] +
                [(3000 + i, 300 + i, 0, 1, 0) for i in range(30)]
            )),
        ])

        write_db(L / "Messages/chat.db", IMESSAGE_SCHEMA, [
            ("INSERT INTO handle VALUES (?,?,?)", [(1, "+390557654321", "iMessage")]),
            ("INSERT INTO chat VALUES (?,?,?)", [(1, "+390557654321", 45)]),
            ("INSERT INTO chat_handle_join VALUES (?,?)", [(1, 1)]),
            ("INSERT INTO message VALUES (?,?,?,?)",
             [(500 + i, 1, apple_ns(days_ago(i * 2)), i % 2) for i in range(10)]),
            ("INSERT INTO chat_message_join VALUES (?,?)",
             [(1, 500 + i) for i in range(10)]),
        ])

        write_db(L / "Application Support/CallHistoryDB/CallHistory.storedata", CALL_SCHEMA, [
            ("INSERT INTO ZCALLRECORD VALUES (?,?,?,?,?,?)", [
                (1, b"+390557654321", apple(days_ago(3)), 1, 320.0, "Marco Bianchi")]),
        ])

        write_db(L / "Calendars/Calendar.sqlitedb", CALENDAR_SCHEMA, [
            ("INSERT INTO CalendarItem VALUES (?,?,?)",
             [(1, "Campionatura AW26", apple(days_ago(11)))]),
            ("INSERT INTO Participant VALUES (?,?,?,?,?,?,?)", [
                (1, 1, 3, "me@studio.it", None, "Me", 1),
                (2, 1, 3, "anna@lanificio.it", None, "Anna Rossi", 0),
            ]),
        ])

        ab = L / "Application Support/AddressBook/Sources/ABCD-1234/AddressBook-v22.abcddb"
        write_db(ab, ADDRESSBOOK_SCHEMA, [
            ("INSERT INTO ZABCDRECORD VALUES (?,?,?,?,?,?)", [
                (1, "UID-1:ABPerson", "Anna", "Rossi", "Lanificio Rossi", "Direttrice creativa"),
                (2, "UID-2:ABPerson", "Marco", "Bianchi", "Filatura Bianchi", "Titolare"),
            ]),
            ("INSERT INTO ZABCDEMAILADDRESS VALUES (?,?,?)", [(1, 1, "anna@lanificio.it")]),
            ("INSERT INTO ZABCDPHONENUMBER VALUES (?,?,?)", [(1, 2, "+39 055 765 4321")]),
            ("INSERT INTO ZABCDSOCIALPROFILE VALUES (?,?,?,?,?)", [
                (1, 1, "LinkedIn", "anna-rossi", "https://www.linkedin.com/in/anna-rossi")]),
            ("INSERT INTO ZABCDURLADDRESS VALUES (?,?,?)", []),
        ])
        # Anna already has a contact photo on disk; Marco does not.
        (ab.parent / "Images").mkdir(parents=True, exist_ok=True)
        (ab.parent / "Images" / "UID-1").write_bytes(b"\x89PNG\r\n\x1a\n")

        cls._real_home = cr.HOME
        cr.HOME = cls.home

    @classmethod
    def tearDownClass(cls):
        cr.HOME = cls._real_home
        shutil.rmtree(cls.home, ignore_errors=True)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cr.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_probe_finds_every_store(self):
        rc, out, _ = self._run(["probe"])
        self.assertEqual(rc, 0)
        for name in ("mail", "imessage", "call", "calendar", "contacts"):
            self.assertIn("[%s]" % name, out)
        self.assertIn("Envelope Index", out)
        self.assertIn("AddressBook-v22.abcddb", out)
        self.assertNotIn("permission denied", out)

    def test_rank_prints_a_table_ordered_by_tie_strength(self):
        rc, out, err = self._run(["rank", "--top", "10"])
        self.assertEqual(rc, 0)
        self.assertIn("Anna Rossi", out)
        self.assertIn("Marco Bianchi", out)
        self.assertNotIn("textilenews", out)     # role sender filtered out
        self.assertNotIn("me@studio.it", out)    # the user is not their own contact
        self.assertLess(out.index("Anna Rossi"), out.index("Marco Bianchi"))
        self.assertIn("events", err)             # per-source summary on stderr

    def test_rank_writes_a_complete_csv(self):
        dest = self.home / "ranking.csv"
        rc, _, err = self._run(["rank", "--out", str(dest)])
        self.assertEqual(rc, 0)
        self.assertIn("contains personal data", err)
        with dest.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(list(rows[0].keys()), cr.COLUMNS)
        # Fixed-precision floats and lowercase booleans, matching the Swift export.
        self.assertRegex(rows[0]["score"], r"^\d+\.\d{3}$")
        self.assertIn(rows[0]["in_contacts"], ("true", "false"))
        anna = next(r for r in rows if r["name"] == "Anna Rossi")
        self.assertEqual(anna["organisation"], "Lanificio Rossi")
        self.assertEqual(anna["linkedin_url"], "https://www.linkedin.com/in/anna-rossi")
        self.assertEqual(anna["has_photo"], "true")
        self.assertIn("calendar", anna["channels"])
        self.assertIn("mail", anna["channels"])

        marco = next(r for r in rows if r["name"] == "Marco Bianchi")
        # Marco is reachable only by phone; the address book fuses the iMessage
        # handle and the call record onto his card, and he still needs a photo.
        self.assertEqual(marco["has_photo"], "false")
        self.assertEqual(marco["linkedin_url"], "")
        self.assertIn("imessage", marco["channels"])
        self.assertIn("call", marco["channels"])
        self.assertEqual(marco["identities"], "557654321")

    def test_source_subsetting(self):
        rc, out, _ = self._run(["rank", "--sources", "call", "--top", "5"])
        self.assertEqual(rc, 0)
        self.assertIn("Marco Bianchi", out)
        self.assertNotIn("Anna Rossi", out)

    def test_json_output_round_trips(self):
        import json
        dest = self.home / "ranking.json"
        rc, _, _ = self._run(["rank", "--json", str(dest)])
        self.assertEqual(rc, 0)
        rows = json.loads(dest.read_text(encoding="utf-8"))
        self.assertTrue(rows)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["name"], "Anna Rossi")

    def test_empty_window_reports_nothing_found(self):
        rc, out, _ = self._run(["rank", "--since", "0"])
        self.assertEqual(rc, 1)
        self.assertIn("No interactions found", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
