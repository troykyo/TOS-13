#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contactrank -- derive a tie-strength ranking of the people you actually
communicate with, from the local stores macOS already keeps on your Mac.

Stage 0 of the TOS-13 relationship-graph toolchain. Read-only, offline,
standard library only, no third-party dependencies, nothing leaves the machine.

Sources (all local, all read-only, all optional):

  mail       ~/Library/Mail/V*/MailData/Envelope Index    (Mail.app)
  imessage   ~/Library/Messages/chat.db                   (Messages)
  call       ~/Library/Application Support/CallHistoryDB/CallHistory.storedata
  calendar   ~/Library/Calendars/Calendar.sqlitedb        (Calendar)
  contacts   ~/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb
  coreduet   ~/Library/Application Support/CoreDuet/People/interactionC.db
             (opt-in; undocumented private store, see --include-coreduet)

Every one of these except `contacts` sits behind macOS TCC and requires Full
Disk Access to be granted to the *terminal application* running this script
(System Settings -> Privacy & Security -> Full Disk Access). Run
`contactrank.py probe` first: it tells you exactly what is reachable and what
is not, and why.

Usage:
    python3 contactrank.py probe
    python3 contactrank.py rank --top 50
    python3 contactrank.py rank --out ranking.csv --json ranking.json
    python3 contactrank.py rank --since 730 --half-life 120 --me you@example.com

Privacy note: `rank --out` writes personal data (names, addresses, phone
numbers, interaction counts) to disk in the clear. Choose the destination
deliberately and treat the file as you would the address book itself.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

APPLE_EPOCH = 978307200  # seconds between 1970-01-01 and 2001-01-01, both UTC
HOME = Path(os.path.expanduser("~"))

# --------------------------------------------------------------------------
# Scoring model (see docs/proposals/0001 for the rationale)
# --------------------------------------------------------------------------
# Weight per (channel, direction). Outbound acts cost the user effort and are
# therefore stronger evidence of an intentional tie than inbound ones, which
# the counterparty can manufacture cheaply (newsletters, notifications, CC).
CHANNEL_WEIGHTS: Dict[Tuple[str, str], float] = {
    ("calendar", "meet"): 6.0,
    ("call", "out"): 5.0,
    ("call", "in"): 4.0,
    ("mail", "out"): 3.0,
    ("mail", "in"): 1.0,
    ("imessage", "out"): 2.0,
    ("imessage", "in"): 1.5,
    ("coreduet", "out"): 1.0,
    ("coreduet", "in"): 0.5,
}

DEFAULT_HALF_LIFE_DAYS = 180.0
DEFAULT_WINDOW_DAYS = 1095  # three years
RECIPROCITY_FLOOR = 0.25    # a wholly one-sided tie retains this share of volume

# Local-parts that denote a machine, a role account or a bulk sender rather
# than a person. Matched case-insensitively against the whole local-part.
ROLE_LOCALPART = re.compile(
    r"^("
    r"no-?reply|do-?not-?reply|donotreply|reply|bounce[s]?|"
    r"mailer-daemon|postmaster|abuse|hostmaster|webmaster|"
    r"notification[s]?|notify|alert[s]?|noc|"
    r"newsletter[s]?|news|marketing|mailing|list|lists|majordomo|"
    r"support|help|helpdesk|service[s]?|contact|info|hello|hi|enquiries|"
    r"admin|administrator|root|daemon|system|automated|auto|robot|bot|"
    r"billing|invoice[s]?|accounts?|payments?|receipts?|orders?|"
    r"security|privacy|legal|compliance|dpo|"
    r"careers|jobs|recruiting|hr|"
    r"updates?|digest|feedback|survey|events?|calendar-notification"
    r")([+._-].*)?$",
    re.IGNORECASE,
)

# Sub-domains and hosts that are structurally transactional.
ROLE_DOMAIN = re.compile(
    r"(^|\.)("
    r"bounce[s]?|mail|email|e?mailer|smtp|mx|reply|notifications?|alerts?|"
    r"sendgrid\.net|amazonses\.com|mailgun\.org|sparkpostmail\.com|"
    r"mcsv\.net|mcdlv\.net|rsgsv\.net|createsend\.com|cmail\d*\.com|"
    r"salesforce\.com|hubspot\.com|intercom-mail\.com|zendesk\.com"
    r")$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Identity normalisation
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def norm_email(raw: Optional[str]) -> Optional[str]:
    """Canonical key for an email address, or None if it is not one."""
    if not raw:
        return None
    # "Name <addr@host>" and '"Name" <addr@host>' -> "addr@host". This has to
    # happen before any <> stripping, or the closing bracket disappears first
    # and the extraction can no longer match.
    s = str(raw).strip()
    m = re.search(r"<([^>]+)>", s)
    if m:
        s = m.group(1)
    s = s.strip().strip("<>").strip().lower()
    if not EMAIL_RE.match(s):
        return None
    return "mailto:" + s


def norm_phone(raw: Optional[str]) -> Optional[str]:
    """
    Canonical key for a phone number.

    We deliberately key on the last nine significant digits rather than on a
    fully-qualified E.164 number. Local stores mix +39 02..., 0039 02...,
    02... and 39 02... for the same person, and resolving those correctly
    needs a country-code library plus knowledge of the user's home region.
    The last nine digits are stable across all of those forms and collide
    only rarely at address-book scale. Documented, not accidental.
    """
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 6:
        return None
    return "tel:" + digits[-9:]


def norm_handle(raw) -> Optional[str]:
    """Normalise an iMessage / CallKit handle, which may be email or phone."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    raw = str(raw).strip()
    if not raw:
        return None
    if "@" in raw:
        return norm_email(raw)
    return norm_phone(raw)


def is_role_address(key: str) -> bool:
    """True for machine/role/bulk senders that should not be ranked as people."""
    if not key.startswith("mailto:"):
        return False
    addr = key[len("mailto:"):]
    local, _, domain = addr.partition("@")
    if ROLE_LOCALPART.match(local):
        return True
    if ROLE_DOMAIN.search(domain):
        return True
    return False


def display_key(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


# --------------------------------------------------------------------------
# Union-find, used to fuse the identities that belong to one human
# --------------------------------------------------------------------------

class DSU:
    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        p = self._parent
        p.setdefault(x, x)
        root = x
        while p[root] != root:
            root = p[root]
        while p[x] != root:  # path compression
            p[x], x = root, p[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def keys(self) -> Iterable[str]:
        return self._parent.keys()


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Event:
    key: str          # normalised identity of the counterparty
    channel: str      # mail | imessage | call | calendar | coreduet
    direction: str    # out | in | meet
    ts: float         # unix seconds, UTC
    audience: int = 1  # number of counterparties addressed at once
    display: Optional[str] = None


@dataclass
class Card:
    """A Contacts.app record."""
    uid: str
    first: str = ""
    last: str = ""
    org: str = ""
    title: str = ""
    emails: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    linkedin: str = ""
    urls: List[str] = field(default_factory=list)
    has_image: bool = False

    @property
    def name(self) -> str:
        n = " ".join(p for p in (self.first, self.last) if p).strip()
        return n or self.org


@dataclass
class Person:
    root: str
    keys: Set[str] = field(default_factory=set)
    displays: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    out_score: float = 0.0
    in_score: float = 0.0
    n_out: int = 0
    n_in: int = 0
    n_meet: int = 0
    n_call: int = 0
    days: Set[str] = field(default_factory=set)
    first_ts: float = math.inf
    last_ts: float = 0.0
    channels: Set[str] = field(default_factory=set)
    card: Optional[Card] = None

    @property
    def volume(self) -> float:
        return self.out_score + self.in_score

    @property
    def reciprocity(self) -> float:
        """
        2*sqrt(O*I)/(O+I): the geometric-to-arithmetic mean ratio of the two
        directional volumes. 1.0 for a perfectly balanced exchange, 0.0 for a
        wholly one-sided one, and -- unlike a raw min/max ratio -- smooth and
        scale-free in between.
        """
        o, i = self.out_score, self.in_score
        if o + i <= 0:
            return 0.0
        return 2.0 * math.sqrt(max(o, 0.0) * max(i, 0.0)) / (o + i)

    @property
    def score(self) -> float:
        return self.volume * (RECIPROCITY_FLOOR + (1.0 - RECIPROCITY_FLOOR) * self.reciprocity)

    @property
    def best_display(self) -> str:
        if self.card and self.card.name:
            return self.card.name
        if self.displays:
            return max(self.displays.items(), key=lambda kv: kv[1])[0]
        return display_key(sorted(self.keys)[0]) if self.keys else "?"


# --------------------------------------------------------------------------
# SQLite access
# --------------------------------------------------------------------------

class SourceError(Exception):
    pass


def _apple_to_unix(v) -> Optional[float]:
    """Core Data / CFAbsoluteTime timestamps, in seconds or nanoseconds."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    if f > 1e17:      # nanoseconds since 2001 (Messages, macOS 10.13+)
        f = f / 1e9
    elif f > 1e14:    # microseconds, seen in some stores
        f = f / 1e6
    return f + APPLE_EPOCH


def _unix_or_apple(v) -> Optional[float]:
    """Mail's Envelope Index stores plain Unix seconds; be tolerant anyway."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    # A plausible Unix timestamp for mail is 1990..2100; anything below that
    # range is almost certainly an Apple-epoch value.
    if f < 631152000:  # 1990-01-01
        return f + APPLE_EPOCH
    return f


class ReadOnlyDB:
    """
    Open a live macOS SQLite store safely.

    These databases are written to continuously by system daemons and are in
    WAL mode. Opening them in place -- even read-only -- can fail on a stale
    lock, and `immutable=1` silently hides everything still in the write-ahead
    log. We therefore snapshot the database together with its -wal and -shm
    sidecars into a temporary directory and read the copy, which is both safe
    for the live store and complete.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._tmp: Optional[str] = None
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise SourceError("not found: %s" % self.path)
        self._tmp = tempfile.mkdtemp(prefix="contactrank-")
        target = Path(self._tmp) / self.path.name
        try:
            shutil.copy2(str(self.path), str(target))
            for suffix in ("-wal", "-shm"):
                side = Path(str(self.path) + suffix)
                if side.exists():
                    shutil.copy2(str(side), str(target) + suffix)
        except PermissionError as exc:
            self.__exit__(None, None, None)
            raise SourceError(
                "permission denied reading %s -- grant Full Disk Access to your "
                "terminal application (System Settings > Privacy & Security > "
                "Full Disk Access), then run this again. (%s)" % (self.path, exc)
            )
        except OSError as exc:
            self.__exit__(None, None, None)
            raise SourceError("cannot copy %s: %s" % (self.path, exc))
        try:
            self.conn = sqlite3.connect(str(target))
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA query_only = 0")  # copy: checkpointing is fine
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            self.__exit__(None, None, None)
            raise SourceError("cannot open %s: %s" % (self.path, exc))
        return self.conn

    def __exit__(self, *exc_info) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute('PRAGMA table_info("%s")' % table.replace('"', '""'))]
    except sqlite3.DatabaseError:
        return []


def has_tables(conn: sqlite3.Connection, *names: str) -> bool:
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(n in present for n in names)


def count_rows(conn: sqlite3.Connection, table: str) -> Optional[int]:
    try:
        return conn.execute('SELECT count(*) FROM "%s"' % table.replace('"', '""')).fetchone()[0]
    except sqlite3.DatabaseError:
        return None


# --------------------------------------------------------------------------
# Source discovery
# --------------------------------------------------------------------------

def find_mail_dbs() -> List[Path]:
    return [Path(p) for p in sorted(glob(str(HOME / "Library/Mail/V*/MailData/Envelope Index")))]


def find_addressbook_dbs() -> List[Path]:
    base = HOME / "Library/Application Support/AddressBook"
    found = []
    top = base / "AddressBook-v22.abcddb"
    if top.exists():
        found.append(top)
    found += [Path(p) for p in sorted(glob(str(base / "Sources/*/AddressBook-v22.abcddb")))]
    return found


def find_messages_db() -> List[Path]:
    p = HOME / "Library/Messages/chat.db"
    return [p] if p.exists() else []


def find_callhistory_db() -> List[Path]:
    p = HOME / "Library/Application Support/CallHistoryDB/CallHistory.storedata"
    return [p] if p.exists() else []


def find_calendar_db() -> List[Path]:
    p = HOME / "Library/Calendars/Calendar.sqlitedb"
    return [p] if p.exists() else []


def find_coreduet_db() -> List[Path]:
    p = HOME / "Library/Application Support/CoreDuet/People/interactionC.db"
    return [p] if p.exists() else []


SOURCES = {
    "mail": find_mail_dbs,
    "imessage": find_messages_db,
    "call": find_callhistory_db,
    "calendar": find_calendar_db,
    "contacts": find_addressbook_dbs,
    "coreduet": find_coreduet_db,
}


# --------------------------------------------------------------------------
# Extractors -- each yields Events and never raises for a merely odd schema
# --------------------------------------------------------------------------

def own_addresses_from_mail(conn: sqlite3.Connection) -> Set[str]:
    """
    Derive the user's own addresses from the mail store itself.

    Two independent signals: the account address embedded in every mailbox
    URL (imap://user%40host@server/...), and the senders of messages that
    live in a Sent mailbox.
    """
    own: Set[str] = set()
    if not has_tables(conn, "mailboxes"):
        return own
    sent_ids: List[int] = []
    for row in conn.execute("SELECT ROWID, url FROM mailboxes"):
        url = row["url"] or ""
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            parsed = None
        if parsed and parsed.username:
            cand = norm_email(urllib.parse.unquote(parsed.username))
            if cand:
                own.add(cand)
        if re.search(r"/sent(\s|%20)?(messages|items|mail)?/?$", url, re.IGNORECASE):
            sent_ids.append(row["ROWID"])
    if sent_ids and has_tables(conn, "messages", "addresses"):
        placeholders = ",".join("?" for _ in sent_ids)
        sql = (
            "SELECT a.address, count(*) c FROM messages m "
            "JOIN addresses a ON a.ROWID = m.sender "
            "WHERE m.mailbox IN (%s) GROUP BY a.address "
            "ORDER BY c DESC LIMIT 25" % placeholders
        )
        try:
            for row in conn.execute(sql, sent_ids):
                cand = norm_email(row["address"])
                if cand:
                    own.add(cand)
        except sqlite3.DatabaseError:
            pass
    return own


def extract_mail(path: Path, since_ts: float, own: Set[str]) -> Iterator[Event]:
    with ReadOnlyDB(path) as conn:
        if not has_tables(conn, "messages", "addresses", "recipients"):
            raise SourceError("unexpected Envelope Index schema in %s" % path)
        own = set(own) | own_addresses_from_mail(conn)

        mcols = set(table_columns(conn, "messages"))
        acols = set(table_columns(conn, "addresses"))
        sname = "sa.comment" if "comment" in acols else "NULL"
        rname = "ra.comment" if "comment" in acols else "NULL"
        ts_expr = "COALESCE(m.date_sent, m.date_received)" if "date_sent" in mcols else "m.date_received"
        automated = " AND COALESCE(m.automated_type, 0) = 0 " if "automated_type" in mcols else ""
        deleted = " AND COALESCE(m.deleted, 0) = 0 " if "deleted" in mcols else ""
        mailbox_join = ""
        mailbox_col = "NULL"
        if "mailbox" in mcols and has_tables(conn, "mailboxes"):
            mailbox_join = "LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox "
            mailbox_col = "mb.url"

        sql = (
            "WITH win AS ("
            "  SELECT m.ROWID AS mid, %s AS ts, m.sender AS sender, %s AS mburl"
            "  FROM messages m %s"
            "  WHERE %s >= ? %s %s"
            "), cnt AS ("
            "  SELECT message, count(*) AS n FROM recipients"
            "  WHERE message IN (SELECT mid FROM win) GROUP BY message"
            ") "
            "SELECT w.mid, w.ts, w.mburl, sa.address AS sender_addr, %s AS sender_name,"
            "       ra.address AS rcpt_addr, %s AS rcpt_name, COALESCE(cnt.n, 1) AS n "
            "FROM win w "
            "LEFT JOIN addresses sa ON sa.ROWID = w.sender "
            "LEFT JOIN recipients r ON r.message = w.mid "
            "LEFT JOIN addresses ra ON ra.ROWID = r.address "
            "LEFT JOIN cnt ON cnt.message = w.mid "
            "ORDER BY w.mid"
            % (ts_expr, mailbox_col, mailbox_join, ts_expr, automated, deleted,
               sname, rname)
        )

        seen_incoming: Optional[int] = None
        for row in conn.execute(sql, (since_ts,)):
            ts = _unix_or_apple(row["ts"])
            if ts is None:
                continue
            sender = norm_email(row["sender_addr"])
            url = row["mburl"] or ""
            is_sent_box = bool(re.search(r"/sent", url, re.IGNORECASE))
            outgoing = (sender is not None and sender in own) or is_sent_box
            audience = max(1, int(row["n"] or 1))

            if outgoing:
                rcpt = norm_email(row["rcpt_addr"])
                if rcpt and rcpt not in own:
                    yield Event(rcpt, "mail", "out", ts, audience, row["rcpt_name"])
            else:
                if sender and sender not in own and seen_incoming != row["mid"]:
                    seen_incoming = row["mid"]
                    yield Event(sender, "mail", "in", ts, audience, row["sender_name"])


def extract_imessage(path: Path, since_ts: float) -> Iterator[Event]:
    with ReadOnlyDB(path) as conn:
        if not has_tables(conn, "message", "handle", "chat", "chat_message_join"):
            raise SourceError("unexpected chat.db schema in %s" % path)

        chat_handles: Dict[int, List[str]] = defaultdict(list)
        if has_tables(conn, "chat_handle_join"):
            for row in conn.execute(
                "SELECT j.chat_id, h.id FROM chat_handle_join j JOIN handle h ON h.ROWID = j.handle_id"
            ):
                k = norm_handle(row[1])
                if k:
                    chat_handles[row[0]].append(k)

        # chat.db stores Apple-epoch seconds before macOS 10.13 and nanoseconds
        # after it. Filter in SQL with the lower (seconds) threshold, which is a
        # superset under either encoding, and narrow exactly in Python once the
        # encoding has been detected per row.
        floor_apple = since_ts - APPLE_EPOCH
        sql = (
            "SELECT m.ROWID AS mid, m.date AS d, m.is_from_me AS me, h.id AS handle, "
            "       cmj.chat_id AS cid "
            "FROM message m "
            "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
            "LEFT JOIN handle h ON h.ROWID = m.handle_id "
            "WHERE m.date >= ?"
        )
        for row in conn.execute(sql, (floor_apple,)):
            ts = _apple_to_unix(row["d"])
            if ts is None or ts < since_ts:
                continue
            participants = chat_handles.get(row["cid"], [])
            audience = max(1, len(participants))
            if row["me"]:
                targets = participants or ([norm_handle(row["handle"])] if row["handle"] else [])
                for t in targets:
                    if t:
                        yield Event(t, "imessage", "out", ts, audience)
            else:
                k = norm_handle(row["handle"])
                if k:
                    yield Event(k, "imessage", "in", ts, audience)


def extract_call(path: Path, since_ts: float) -> Iterator[Event]:
    with ReadOnlyDB(path) as conn:
        if not has_tables(conn, "ZCALLRECORD"):
            raise SourceError("unexpected CallHistory schema in %s" % path)
        cols = set(table_columns(conn, "ZCALLRECORD"))
        if not {"ZADDRESS", "ZDATE"} <= cols:
            raise SourceError("CallHistory lacks ZADDRESS/ZDATE in %s" % path)
        originated = "ZORIGINATED" if "ZORIGINATED" in cols else "NULL"
        name = "ZNAME" if "ZNAME" in cols else "NULL"
        sql = (
            "SELECT ZADDRESS AS addr, ZDATE AS d, %s AS originated, %s AS nm "
            "FROM ZCALLRECORD WHERE ZDATE >= ?" % (originated, name)
        )
        for row in conn.execute(sql, (since_ts - APPLE_EPOCH,)):
            ts = _apple_to_unix(row["d"])
            if ts is None or ts < since_ts:
                continue
            k = norm_handle(row["addr"])
            if not k:
                continue
            direction = "out" if row["originated"] else "in"
            yield Event(k, "call", direction, ts, 1, row["nm"])


def extract_calendar(path: Path, since_ts: float, max_attendees: int = 25) -> Iterator[Event]:
    with ReadOnlyDB(path) as conn:
        if not has_tables(conn, "CalendarItem", "Participant"):
            raise SourceError("unexpected Calendar schema in %s" % path)
        pcols = set(table_columns(conn, "Participant"))
        icols = set(table_columns(conn, "CalendarItem"))
        if "owner_id" not in pcols or "start_date" not in icols:
            raise SourceError("Calendar schema lacks owner_id/start_date in %s" % path)
        email_col = "p.email" if "email" in pcols else "NULL"
        phone_col = "p.phone_number" if "phone_number" in pcols else "NULL"
        name_col = "p.display_name" if "display_name" in pcols else "NULL"
        self_col = "COALESCE(p.is_self, 0)" if "is_self" in pcols else "0"

        sql = (
            "SELECT ci.ROWID AS eid, ci.start_date AS d, %s AS em, %s AS ph, %s AS nm, %s AS isself "
            "FROM Participant p JOIN CalendarItem ci ON ci.ROWID = p.owner_id "
            "WHERE ci.start_date >= ? ORDER BY ci.ROWID"
            % (email_col, phone_col, name_col, self_col)
        )
        bucket: List[Tuple[str, Optional[str]]] = []
        current: Optional[int] = None
        current_ts: Optional[float] = None

        def flush() -> Iterator[Event]:
            if current_ts is None or not bucket:
                return
            if len(bucket) > max_attendees:
                return
            for k, nm in bucket:
                yield Event(k, "calendar", "meet", current_ts, len(bucket), nm)

        for row in conn.execute(sql, (since_ts - APPLE_EPOCH,)):
            if row["eid"] != current:
                for ev in flush():
                    yield ev
                bucket = []
                current = row["eid"]
                current_ts = _apple_to_unix(row["d"])
            if row["isself"]:
                continue
            k = norm_email(row["em"]) or norm_phone(row["ph"])
            if k:
                bucket.append((k, row["nm"]))
        for ev in flush():
            yield ev


def _coreduet_join_table(conn: sqlite3.Connection) -> Optional[Tuple[str, str, str]]:
    """Find the Core Data many-to-many table linking ZINTERACTIONS to ZCONTACTS."""
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z\\_%' ESCAPE '\\'"
    ):
        cols = table_columns(conn, name)
        icol = next((c for c in cols if c.upper().endswith("INTERACTIONS")), None)
        ccol = next((c for c in cols if c.upper().endswith("CONTACTS")), None)
        if icol and ccol:
            return name, icol, ccol
    return None


def extract_coreduet(path: Path, since_ts: float) -> Iterator[Event]:
    with ReadOnlyDB(path) as conn:
        if not has_tables(conn, "ZINTERACTIONS", "ZCONTACTS"):
            raise SourceError("unexpected interactionC schema in %s" % path)
        jt = _coreduet_join_table(conn)
        if not jt:
            raise SourceError("no ZINTERACTIONS<->ZCONTACTS join table in %s" % path)
        table, icol, ccol = jt
        ccols = set(table_columns(conn, "ZCONTACTS"))
        ident = "c.ZIDENTIFIER" if "ZIDENTIFIER" in ccols else "NULL"
        disp = "c.ZDISPLAYNAME" if "ZDISPLAYNAME" in ccols else "NULL"
        sql = (
            'SELECT i.ZSTARTDATE AS d, i.ZDIRECTION AS dir, %s AS ident, %s AS nm '
            'FROM "%s" j '
            'JOIN ZINTERACTIONS i ON i.Z_PK = j."%s" '
            'JOIN ZCONTACTS c ON c.Z_PK = j."%s" '
            "WHERE i.ZSTARTDATE >= ?" % (ident, disp, table, icol, ccol)
        )
        for row in conn.execute(sql, (since_ts - APPLE_EPOCH,)):
            ts = _apple_to_unix(row["d"])
            if ts is None or ts < since_ts:
                continue
            k = norm_handle(row["ident"])
            if not k:
                continue
            direction = "out" if row["dir"] else "in"
            yield Event(k, "coreduet", direction, ts, 1, row["nm"])


def extract_cards(paths: Sequence[Path]) -> List[Card]:
    cards: List[Card] = []
    for path in paths:
        try:
            with ReadOnlyDB(path) as conn:
                if not has_tables(conn, "ZABCDRECORD"):
                    continue
                rcols = set(table_columns(conn, "ZABCDRECORD"))
                sel = ["Z_PK"]
                for c in ("ZUNIQUEID", "ZFIRSTNAME", "ZLASTNAME", "ZORGANIZATION", "ZJOBTITLE"):
                    sel.append(c if c in rcols else "NULL AS %s" % c)
                by_pk: Dict[int, Card] = {}
                for row in conn.execute("SELECT %s FROM ZABCDRECORD" % ", ".join(sel)):
                    uid = row["ZUNIQUEID"] or ("%s#%s" % (path.parent.name, row["Z_PK"]))
                    by_pk[row["Z_PK"]] = Card(
                        uid=str(uid),
                        first=(row["ZFIRSTNAME"] or "").strip(),
                        last=(row["ZLASTNAME"] or "").strip(),
                        org=(row["ZORGANIZATION"] or "").strip(),
                        title=(row["ZJOBTITLE"] or "").strip(),
                    )

                if has_tables(conn, "ZABCDEMAILADDRESS"):
                    for row in conn.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                        c = by_pk.get(row["ZOWNER"])
                        k = norm_email(row["ZADDRESS"])
                        if c and k:
                            c.emails.append(k)
                if has_tables(conn, "ZABCDPHONENUMBER"):
                    for row in conn.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                        c = by_pk.get(row["ZOWNER"])
                        k = norm_phone(row["ZFULLNUMBER"])
                        if c and k:
                            c.phones.append(k)
                if has_tables(conn, "ZABCDSOCIALPROFILE"):
                    scols = set(table_columns(conn, "ZABCDSOCIALPROFILE"))
                    svc = "ZSERVICE" if "ZSERVICE" in scols else "NULL"
                    url = "ZURL" if "ZURL" in scols else "NULL"
                    usr = "ZUSERNAME" if "ZUSERNAME" in scols else "NULL"
                    for row in conn.execute(
                        "SELECT ZOWNER, %s AS svc, %s AS url, %s AS usr FROM ZABCDSOCIALPROFILE"
                        % (svc, url, usr)
                    ):
                        c = by_pk.get(row["ZOWNER"])
                        if not c:
                            continue
                        blob = " ".join(str(x) for x in (row["svc"], row["url"], row["usr"]) if x)
                        if "linkedin" in blob.lower():
                            c.linkedin = (row["url"] or row["usr"] or "").strip()
                if has_tables(conn, "ZABCDURLADDRESS"):
                    for row in conn.execute("SELECT ZOWNER, ZURL FROM ZABCDURLADDRESS"):
                        c = by_pk.get(row["ZOWNER"])
                        if c and row["ZURL"]:
                            u = str(row["ZURL"]).strip()
                            c.urls.append(u)
                            if "linkedin.com" in u.lower() and not c.linkedin:
                                c.linkedin = u

                images = set()
                img_dir = path.parent / "Images"
                if img_dir.is_dir():
                    try:
                        images = {p.name for p in img_dir.iterdir()}
                    except OSError:
                        images = set()
                for c in by_pk.values():
                    stem = c.uid.split(":")[0]
                    c.has_image = any(n.startswith(stem) for n in images)

                cards.extend(c for c in by_pk.values() if c.emails or c.phones or c.name)
        except SourceError:
            continue
    return cards


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def audience_damping(n: int) -> float:
    """
    A message addressed to n counterparties carries 1/(1+log2(n)) of the
    evidential weight of a one-to-one message. Broadcasts decay smoothly
    rather than being cut off at an arbitrary threshold.
    """
    n = max(1, int(n))
    return 1.0 / (1.0 + math.log2(n))


def aggregate(
    events: Iterable[Event],
    cards: Sequence[Card],
    now: float,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    drop_role_addresses: bool = True,
) -> List[Person]:
    lam = math.log(2.0) / (half_life_days * 86400.0)

    dsu = DSU()
    key_to_card: Dict[str, Card] = {}
    for card in cards:
        keys = list(dict.fromkeys(card.emails + card.phones))
        for k in keys:
            key_to_card.setdefault(k, card)
        for k in keys[1:]:
            dsu.union(keys[0], k)

    people: Dict[str, Person] = {}
    for ev in events:
        if drop_role_addresses and is_role_address(ev.key):
            continue
        root = dsu.find(ev.key)
        p = people.get(root)
        if p is None:
            p = people[root] = Person(root=root)
        p.keys.add(ev.key)
        p.channels.add(ev.channel)
        if ev.display:
            nm = str(ev.display).strip()
            if nm and "@" not in nm:
                p.displays[nm] += 1

        w = CHANNEL_WEIGHTS.get((ev.channel, ev.direction), 0.0)
        if w <= 0.0:
            continue
        w *= audience_damping(ev.audience)
        w *= math.exp(-lam * max(0.0, now - ev.ts))

        if ev.direction == "meet":
            # A meeting is inherently mutual: split it across both directions
            # so that it strengthens the tie without distorting reciprocity.
            p.out_score += w / 2.0
            p.in_score += w / 2.0
            p.n_meet += 1
        elif ev.direction == "out":
            p.out_score += w
            p.n_out += 1
            if ev.channel == "call":
                p.n_call += 1
        else:
            p.in_score += w
            p.n_in += 1
            if ev.channel == "call":
                p.n_call += 1

        p.days.add(datetime.fromtimestamp(ev.ts, timezone.utc).strftime("%Y-%m-%d"))
        p.first_ts = min(p.first_ts, ev.ts)
        p.last_ts = max(p.last_ts, ev.ts)

    for root, p in people.items():
        for k in p.keys:
            if k in key_to_card:
                p.card = key_to_card[k]
                break

    # Descending score, ties broken on the display name so that the output is
    # deterministic and diffable against the Swift implementation, which cannot
    # rely on a stable sort.
    return sorted(people.values(), key=lambda x: (-x.score, x.best_display))


def filter_people(people: Sequence[Person], min_active_days: int) -> List[Person]:
    """
    Drop one-shot bursts. A tie is evidenced by recurrence over distinct days,
    not by a single busy afternoon -- except where a meeting or a phone call
    took place, which is strong enough on its own.
    """
    out = []
    for p in people:
        if len(p.days) >= min_active_days or p.n_meet > 0 or p.n_call > 0:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def iso(ts: float) -> str:
    if not ts or ts in (math.inf, 0.0):
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def person_row(rank: int, p: Person) -> Dict[str, object]:
    card = p.card
    return {
        "rank": rank,
        "name": p.best_display,
        "organisation": card.org if card else "",
        "title": card.title if card else "",
        "score": round(p.score, 3),
        "volume": round(p.volume, 3),
        "reciprocity": round(p.reciprocity, 3),  # numeric here; CSV formats via csv_cell
        "sent": p.n_out,
        "received": p.n_in,
        "meetings": p.n_meet,
        "calls": p.n_call,
        "active_days": len(p.days),
        "first_seen": iso(p.first_ts),
        "last_seen": iso(p.last_ts),
        "channels": "+".join(sorted(p.channels)),
        "identities": ";".join(display_key(k) for k in sorted(p.keys)),
        "contact_uid": card.uid if card else "",
        "in_contacts": bool(card),
        "has_photo": bool(card.has_image) if card else False,
        "linkedin_url": card.linkedin if card else "",
    }


def csv_cell(value: object) -> str:
    """
    Render one value for CSV. Booleans as lowercase and floats at fixed
    precision, so that this file and the Swift app's export are byte-identical
    for the same input and can be diffed against each other on one machine.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "%.3f" % value
    return str(value)


COLUMNS = [
    "rank", "name", "organisation", "title", "score", "volume", "reciprocity",
    "sent", "received", "meetings", "calls", "active_days", "first_seen",
    "last_seen", "channels", "identities", "contact_uid", "in_contacts",
    "has_photo", "linkedin_url",
]


def print_table(rows: Sequence[Dict[str, object]], limit: int) -> None:
    show = ["rank", "name", "organisation", "score", "reciprocity",
            "sent", "received", "meetings", "active_days", "last_seen", "linkedin_url"]
    head = {"rank": "#", "name": "Name", "organisation": "Organisation",
            "score": "Score", "reciprocity": "Recip", "sent": "Out", "received": "In",
            "meetings": "Mtg", "active_days": "Days", "last_seen": "Last",
            "linkedin_url": "LinkedIn"}
    def cell(r, c):
        if c == "linkedin_url":
            return "known" if r[c] else "-"
        return str(r[c])

    data = [[head[c] for c in show]]
    for r in rows[:limit]:
        data.append([cell(r, c) for c in show])
    widths = [max(len(row[i]) for row in data) for i in range(len(show))]
    for i, row in enumerate(data):
        line = "  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row))
        print(line.rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

PROBE_HINTS = {
    "mail": "Mail.app store. Empty if you use Spark, Outlook or a web client.",
    "imessage": "Messages store. Needs Full Disk Access.",
    "call": "FaceTime/iPhone call history synced through Continuity.",
    "calendar": "Meeting co-attendance -- the strongest professional signal.",
    "contacts": "Address book: names, organisations and any LinkedIn URLs you already hold.",
    "coreduet": "Undocumented private store behind Siri's people suggestions. Opt-in.",
}


def cmd_probe(args: argparse.Namespace) -> int:
    print("contactrank probe -- macOS local interaction stores\n")
    if sys.platform != "darwin":
        print("NOTE: this is not macOS (%s). Paths below will not exist here;\n"
              "      run this on the Mac whose contacts you want to rank.\n" % sys.platform)
    any_blocked = False
    for name, finder in SOURCES.items():
        paths = finder()
        print("[%s] %s" % (name, PROBE_HINTS[name]))
        if not paths:
            print("    - not present")
            print()
            continue
        for path in paths:
            try:
                with ReadOnlyDB(path) as conn:
                    tables = sorted(
                        r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'")
                    )
                    interesting = [t for t in tables if t in (
                        "messages", "addresses", "recipients", "mailboxes",
                        "message", "handle", "chat", "chat_message_join",
                        "ZCALLRECORD", "CalendarItem", "Participant",
                        "ZABCDRECORD", "ZABCDEMAILADDRESS", "ZABCDSOCIALPROFILE",
                        "ZINTERACTIONS", "ZCONTACTS")]
                    print("    + %s" % path)
                    print("      %d tables; relevant: %s" % (
                        len(tables), ", ".join(
                            "%s=%s" % (t, count_rows(conn, t)) for t in interesting) or "none"))
            except SourceError as exc:
                any_blocked = any_blocked or "permission denied" in str(exc)
                print("    ! %s" % exc)
        print()
    if any_blocked:
        print("Some stores were unreadable. Grant Full Disk Access to the application")
        print("running this script (Terminal, iTerm, your IDE) in")
        print("System Settings > Privacy & Security > Full Disk Access, then re-run.")
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc).timestamp()
    since_ts = now - args.since * 86400.0
    own = {k for k in (norm_email(a) for a in (args.me or [])) if k}

    wanted = set(args.sources.split(",")) if args.sources else {
        "mail", "imessage", "call", "calendar"}
    if args.include_coreduet:
        wanted.add("coreduet")

    events: List[Event] = []
    notes: List[str] = []

    def collect(name: str, gen) -> None:
        try:
            n0 = len(events)
            events.extend(gen)
            notes.append("%-9s %7d events" % (name, len(events) - n0))
        except SourceError as exc:
            notes.append("%-9s skipped: %s" % (name, exc))

    if "mail" in wanted:
        for p in find_mail_dbs():
            collect("mail", extract_mail(p, since_ts, own))
    if "imessage" in wanted:
        for p in find_messages_db():
            collect("imessage", extract_imessage(p, since_ts))
    if "call" in wanted:
        for p in find_callhistory_db():
            collect("call", extract_call(p, since_ts))
    if "calendar" in wanted:
        for p in find_calendar_db():
            collect("calendar", extract_calendar(p, since_ts))
    if "coreduet" in wanted:
        for p in find_coreduet_db():
            collect("coreduet", extract_coreduet(p, since_ts))

    cards = extract_cards(find_addressbook_dbs())
    notes.append("%-9s %7d cards" % ("contacts", len(cards)))

    people = aggregate(events, cards, now,
                       half_life_days=args.half_life,
                       drop_role_addresses=not args.keep_role_addresses)
    people = filter_people(people, args.min_active_days)
    rows = [person_row(i + 1, p) for i, p in enumerate(people)]

    if not args.quiet:
        for line in notes:
            print("  " + line, file=sys.stderr)
        print("  %-9s %7d people after filtering\n" % ("ranked", len(rows)), file=sys.stderr)

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COLUMNS)
            for r in rows:
                w.writerow([csv_cell(r[c]) for c in COLUMNS])
        print("wrote %s (%d rows) -- contains personal data, handle accordingly"
              % (args.out, len(rows)), file=sys.stderr)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
        print("wrote %s -- contains personal data, handle accordingly" % args.json,
              file=sys.stderr)
    if not args.out and not args.json:
        if not rows:
            print("No interactions found. Run `probe` to see which stores are readable.")
            return 1
        print_table(rows, args.top)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="contactrank",
        description="Rank the people you actually communicate with, from local macOS stores.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="report which local stores exist and are readable")
    p.set_defaults(func=cmd_probe)

    r = sub.add_parser("rank", help="compute and print/write the ranking")
    r.add_argument("--since", type=int, default=DEFAULT_WINDOW_DAYS,
                   metavar="DAYS", help="observation window (default: %(default)s)")
    r.add_argument("--half-life", type=float, default=DEFAULT_HALF_LIFE_DAYS,
                   metavar="DAYS", help="recency half-life (default: %(default)s)")
    r.add_argument("--min-active-days", type=int, default=3, metavar="N",
                   help="minimum distinct days of contact, unless a call or "
                        "meeting occurred (default: %(default)s)")
    r.add_argument("--top", type=int, default=50, metavar="N",
                   help="rows to print when not writing a file (default: %(default)s)")
    r.add_argument("--me", action="append", metavar="ADDRESS",
                   help="one of your own email addresses; repeatable. Usually "
                        "detected automatically from the Mail store.")
    r.add_argument("--sources", metavar="LIST",
                   help="comma-separated subset of mail,imessage,call,calendar")
    r.add_argument("--include-coreduet", action="store_true",
                   help="also read the private CoreDuet interaction store. Its "
                        "direction encoding is undocumented and varies between "
                        "macOS releases; treat the result as corroborating only.")
    r.add_argument("--keep-role-addresses", action="store_true",
                   help="do not filter no-reply/notifications/billing senders")
    r.add_argument("--out", metavar="FILE.csv", help="write the full ranking as CSV")
    r.add_argument("--json", metavar="FILE.json", help="write the full ranking as JSON")
    r.add_argument("--quiet", action="store_true", help="suppress the per-source summary")
    r.set_defaults(func=cmd_rank)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
