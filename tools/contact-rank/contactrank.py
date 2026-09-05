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
    python3 contactrank.py stats
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
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import urllib.parse
from collections import defaultdict
from functools import lru_cache
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

# An unambiguous machine token appearing anywhere in the local part, bounded by
# a delimiter. The anchored pattern above only matches a local part that *is* a
# role name; real bulk senders prefix it -- scholaralerts-noreply, jobalerts-
# noreply, news-noreply. Deliberately narrower than the list above, because this
# one can match inside a name and must not.
# Only tokens that can never be part of a person's name. "alerts", "newsletter"
# and "digest" were here and were removed: they can sit inside one, and the
# structural broadcaster rule catches those senders anyway without the risk.
# When a lexical rule and a structural one overlap, keep the structural one.
ROLE_TOKEN_ANYWHERE = re.compile(
    r"(^|[.\-_+])("
    r"no-?reply|do-?not-?reply|donotreply|noreply|"
    r"mailer-daemon|postmaster|bounce[s]?|notification[s]?"
    r")([.\-_+]|$)",
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


@lru_cache(maxsize=200_000)
def norm_email(raw: Optional[str]) -> Optional[str]:
    """
    Canonical key for an email address, or None if it is not one.

    Memoised: a large mailbox yields millions of recipient rows drawn from only
    tens of thousands of distinct addresses, so almost all of this work is
    repeated. The cache turns the dominant cost of a run into a dict lookup.
    """
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


@lru_cache(maxsize=200_000)
def norm_phone(raw: Optional[str]) -> Optional[str]:
    """
    Canonical key for a phone number. Memoised, as above.

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
    if ROLE_TOKEN_ANYWHERE.search(local):
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

# Where each store lives, as glob patterns relative to the home directory.
#
# Apple moves these between releases: Calendar migrated into a group container,
# Mail's version directory changes every few majors. So each store is a list of
# candidates tried in order, and `probe` reports what it searched when it finds
# nothing -- a wrong guess should be visible, not silent.
SEARCH_PATHS = {
    "mail": [
        "Library/Mail/V*/MailData/Envelope Index",
    ],
    "imessage": [
        "Library/Messages/chat.db",
    ],
    "call": [
        "Library/Application Support/CallHistoryDB/CallHistory.storedata",
    ],
    "calendar": [
        # Sonoma and later; the old location is kept below for earlier systems.
        "Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb",
        "Library/Calendars/Calendar.sqlitedb",
        "Library/Containers/com.apple.CalendarAgent/Data/Library/Calendars/Calendar.sqlitedb",
        "Library/Group Containers/*/Calendar.sqlitedb",
    ],
    "contacts": [
        "Library/Application Support/AddressBook/AddressBook-v22.abcddb",
        "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb",
    ],
    "coreduet": [
        "Library/Application Support/CoreDuet/People/interactionC.db",
        "Library/Group Containers/*/CoreDuet/People/interactionC.db",
    ],
}


def natural_key(text: str) -> List[object]:
    """Sort V9 before V10. Lexicographic order puts V10 first, which would make
    'the newest version directory' select the oldest one."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(text))]


def find_dbs(store: str) -> List[Path]:
    """
    Resolve a store to the databases actually present.

    `contacts` is the one store where several files are genuinely different
    accounts and all of them count. Everywhere else a second match means a copy
    left behind by a macOS upgrade -- two Mail version directories, a Calendar
    store in both its old and new home -- and reading both would count every
    interaction twice. So contacts takes everything; the rest take the newest
    match of the first pattern that hits.
    """
    if store == "contacts":
        found: List[Path] = []
        for pattern in SEARCH_PATHS[store]:
            found += sorted((Path(p) for p in glob(str(HOME / pattern))), key=natural_key)
        return found
    for pattern in SEARCH_PATHS[store]:
        found = sorted((Path(p) for p in glob(str(HOME / pattern))), key=natural_key)
        if found:
            return found[-1:]
    return []


def searched_paths(store: str) -> List[str]:
    return [str(HOME / pattern) for pattern in SEARCH_PATHS[store]]


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

                # Contact photographs are files beside the store, named by the
                # record's UUID. Index them by every plausible key first: the
                # naive prefix scan is O(records x images), which on a real
                # address book of 80,000 records is a quarter of a billion
                # string comparisons.
                image_keys: Set[str] = set()
                img_dir = path.parent / "Images"
                if img_dir.is_dir():
                    try:
                        for name in (q.name for q in img_dir.iterdir()):
                            image_keys.add(name)
                            image_keys.add(name.split(":")[0])
                            if len(name) >= 36:
                                image_keys.add(name[:36])
                    except OSError:
                        pass
                if image_keys:
                    for c in by_pk.values():
                        c.has_image = c.uid.split(":")[0] in image_keys

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


def is_broadcaster(p: Person) -> bool:
    """
    Someone you have never written to -- or write to less than once in twenty --
    is a broadcaster, however much they send you.

    This is structural rather than lexical, which is the point: keyword lists
    only ever catch the senders someone thought of, and every real newsletter
    that slipped through the list did so because its address looked like a
    person's. Volume of inbound mail is not evidence of a relationship; a reply
    is. A call or a meeting overrides it outright.
    """
    if p.n_meet or p.n_call:
        return False
    if p.n_out == 0 and p.n_in >= 5:
        return True
    if p.n_in >= 20 and (p.n_out / float(p.n_in)) < 0.05:
        return True
    return False


def filter_people(people: Sequence[Person], min_active_days: int,
                  drop_broadcasters: bool = True) -> List[Person]:
    """
    Drop one-shot bursts. A tie is evidenced by recurrence over distinct days,
    not by a single busy afternoon -- except where a meeting or a phone call
    took place, which is strong enough on its own.
    """
    out = []
    for p in people:
        if drop_broadcasters and is_broadcaster(p):
            continue
        if len(p.days) >= min_active_days or p.n_meet > 0 or p.n_call > 0:
            out.append(p)
    return out


def fold_name(name: str) -> str:
    """Case-, accent- and spacing-insensitive form of a display name."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def likely_duplicates(people: Sequence[Person]) -> List[List[Person]]:
    """
    People who look like one person split across two identities.

    Identity fusion only merges what a single address-book card links together,
    so someone who writes from a second address that is not on their card
    appears twice, with their tie strength divided between the halves. The two
    shapes this takes in practice are an identical name, and one name that is a
    prefix of a longer one -- a married or double surname added later.

    This reports rather than merges. Fusing on a name alone would silently
    combine homonyms, and the real repair is to merge the cards in Contacts,
    which fixes every future run and everything else that reads the address
    book. `--fuse-by-name` is available for when you would rather not.
    """
    by_name: Dict[str, List[Person]] = defaultdict(list)
    for p in people:
        folded = fold_name(p.best_display)
        if folded:
            by_name[folded].append(p)

    groups: List[List[Person]] = [g for g in by_name.values() if len(g) > 1]
    claimed = {id(p) for g in groups for p in g}

    # "Bruna Goveia" and "Bruna Goveia da Rocha": one name extending another at
    # a word boundary, which an exact match cannot see.
    names = sorted(by_name)
    for i, short in enumerate(names):
        if len(short.split()) < 2:
            continue
        for long in names[i + 1:]:
            if not long.startswith(short + " "):
                break
            merged = [p for p in by_name[short] + by_name[long] if id(p) not in claimed]
            if len(merged) > 1:
                groups.append(merged)
                claimed.update(id(p) for p in merged)
    return groups


def fuse_by_name(people: Sequence[Person]) -> List[Person]:
    """Merge the groups `likely_duplicates` finds. Opt-in: see its docstring."""
    groups = likely_duplicates(people)
    absorbed = {id(p) for g in groups for p in g[1:]}
    merged: List[Person] = []
    for group in groups:
        head = group[0]
        for other in group[1:]:
            head.keys |= other.keys
            head.channels |= other.channels
            head.days |= other.days
            for name, n in other.displays.items():
                head.displays[name] += n
            head.out_score += other.out_score
            head.in_score += other.in_score
            head.n_out += other.n_out
            head.n_in += other.n_in
            head.n_meet += other.n_meet
            head.n_call += other.n_call
            head.first_ts = min(head.first_ts, other.first_ts)
            head.last_ts = max(head.last_ts, other.last_ts)
            head.card = head.card or other.card
        merged.append(head)
    heads = {id(p) for p in merged}
    kept = [p for p in people if id(p) not in absorbed and id(p) not in heads]
    return sorted(kept + merged, key=lambda x: (-x.score, x.best_display))


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
    for name in SEARCH_PATHS:
        paths = find_dbs(name)
        print("[%s] %s" % (name, PROBE_HINTS[name]))
        if not paths:
            print("    - not present. Searched:")
            for pattern in searched_paths(name):
                print("        %s" % pattern)
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
                        "ZABCDRECORD", "ZABCDEMAILADDRESS", "ZABCDPHONENUMBER",
                        "ZABCDSOCIALPROFILE",
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


def gather(args: argparse.Namespace, now: float) -> Tuple[List[Event], List[Card], List[str]]:
    """Read every enabled source. Shared by `rank` and `stats`."""
    since_ts = now - args.since * 86400.0
    own = {k for k in (norm_email(a) for a in (getattr(args, "me", None) or [])) if k}

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
        for p in find_dbs("mail"):
            collect("mail", extract_mail(p, since_ts, own))
    if "imessage" in wanted:
        for p in find_dbs("imessage"):
            collect("imessage", extract_imessage(p, since_ts))
    if "call" in wanted:
        for p in find_dbs("call"):
            collect("call", extract_call(p, since_ts))
    if "calendar" in wanted:
        for p in find_dbs("calendar"):
            collect("calendar", extract_calendar(p, since_ts))
    if "coreduet" in wanted:
        for p in find_dbs("coreduet"):
            collect("coreduet", extract_coreduet(p, since_ts))

    cards = extract_cards(find_dbs("contacts"))
    notes.append("%-9s %7d cards" % ("contacts", len(cards)))
    return events, cards, notes


def cmd_rank(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc).timestamp()
    events, cards, notes = gather(args, now)

    people = aggregate(events, cards, now,
                       half_life_days=args.half_life,
                       drop_role_addresses=not args.keep_role_addresses)
    people = filter_people(people, args.min_active_days,
                           drop_broadcasters=not args.keep_broadcasters)
    if args.fuse_by_name:
        people = fuse_by_name(people)
    rows = [person_row(i + 1, p) for i, p in enumerate(people)]

    if args.duplicates:
        groups = likely_duplicates(people)
        if not groups:
            print("No likely duplicates.")
            return 0
        print("%d likely duplicate(s). Merging the cards in Contacts fixes these"
              % len(groups))
        print("at the source, for this tool and everything else.\n")
        for group in sorted(groups, key=lambda g: -sum(p.score for p in g)):
            print("  %s" % group[0].best_display)
            for p in group:
                print("      %-8.1f %s" % (p.score, ", ".join(
                    display_key(k) for k in sorted(p.keys))))
        return 0

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


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def redact_path(path: object) -> str:
    """Replace the home directory with ~, so paths carry no account name."""
    return str(path).replace(str(HOME), "~")


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile. No dependencies, and exact enough for a report."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[k]


def cmd_stats(args: argparse.Namespace) -> int:
    """
    A diagnostic report containing no personal data.

    The report you want to share when asking whether a run looks right. It
    carries counts, distributions, schema fingerprints and errors, and no names,
    addresses, phone numbers, organisations or domains -- so sending it somewhere
    does not disclose your contacts, who are third parties who never agreed to
    that. `rank --out` is the one that writes personal data, and it says so.
    """
    now = datetime.now(timezone.utc).timestamp()
    out = []

    def say(line: str = "") -> None:
        out.append(line)

    say("contactrank stats -- diagnostic report, contains no personal data")
    say()
    say("environment")
    mac = platform.mac_ver()[0]
    say("  platform          %s" % (("macOS " + mac) if mac else platform.system()))
    say("  python            %s" % platform.python_version())
    say("  sqlite            %s" % sqlite3.sqlite_version)
    say()

    say("stores")
    for name in SEARCH_PATHS:
        paths = find_dbs(name)
        if not paths:
            say("  %-10s not present; searched %d location(s)"
                % (name, len(SEARCH_PATHS[name])))
            continue
        for path in paths:
            try:
                with ReadOnlyDB(path) as conn:
                    tables = sorted(
                        r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"))
                    counts = ["%s=%s" % (t, count_rows(conn, t)) for t in tables
                              if count_rows(conn, t)]
                    say("  %-10s %s" % (name, redact_path(path)))
                    say("  %-10s %d tables; %s" % ("", len(tables),
                                                   " ".join(counts[:8]) or "empty"))
            except SourceError as exc:
                say("  %-10s %s" % (name, redact_path(exc)))
    say()

    events, cards, notes = gather(args, now)
    say("sources")
    for line in notes:
        say("  " + line)
    say()

    say("address book")
    say("  %-24s %6d" % ("cards", len(cards)))
    say("  %-24s %6d" % ("with an email", sum(1 for c in cards if c.emails)))
    say("  %-24s %6d" % ("with a phone", sum(1 for c in cards if c.phones)))
    say("  %-24s %6d" % ("with a photo", sum(1 for c in cards if c.has_image)))
    say("  %-24s %6d   <- the Stage B head start"
        % ("with a LinkedIn URL", sum(1 for c in cards if c.linkedin)))
    say()

    role = {e.key for e in events if is_role_address(e.key)}
    unfiltered = aggregate(events, cards, now, half_life_days=args.half_life,
                           drop_role_addresses=not args.keep_role_addresses)
    people = filter_people(unfiltered, args.min_active_days,
                           drop_broadcasters=not args.keep_broadcasters)

    say("ranking (window %dd, half-life %.0fd, min-active-days %d)"
        % (args.since, args.half_life, args.min_active_days))
    say("  people ranked            %6d" % len(people))
    say("  dropped as one-shot      %6d" % (len(unfiltered) - len(people)))
    say("  role senders suppressed  %6d distinct addresses" % len(role))
    say()

    if not people:
        say("No one ranked. If the stores above show rows but the sources show few")
        say("events, the window may be too narrow, or a schema may have moved.")
        print("\n".join(out))
        return 1

    scores = [p.score for p in people]
    say("score distribution")
    for label, q in (("max", 1.0), ("p90", 0.9), ("median", 0.5), ("p10", 0.1), ("min", 0.0)):
        say("  %-8s %10.1f" % (label, percentile(scores, q)))
    say()

    total = len(people)

    def share(label: str, n: int) -> None:
        say("  %-24s %6d / %d  (%3.0f%%)" % (label, n, total, 100.0 * n / total))

    top = people[:50]
    say("coverage")
    share("in address book", sum(1 for p in people if p.card))
    share("has a photo", sum(1 for p in people if p.card and p.card.has_image))
    share("has a LinkedIn URL", sum(1 for p in people if p.card and p.card.linkedin))
    say("  %-24s %6d   -- of the top 50: %d"
        % ("NOT in address book", sum(1 for p in people if not p.card),
           sum(1 for p in top if not p.card)))
    say()

    dupes = likely_duplicates(people)
    if dupes:
        say("  %-24s %6d groups, %d people   <- `rank --duplicates`"
            % ("likely duplicates", len(dupes), sum(len(g) for g in dupes)))
    say()

    say("identities")
    say("  %-24s %6d" % ("people with >1 identity",
                         sum(1 for p in people if len(p.keys) > 1)))
    say("  %-24s %6d" % ("phone-only",
                         sum(1 for p in people
                             if all(k.startswith("tel:") for k in p.keys))))
    say("  %-24s %6d" % ("email-only",
                         sum(1 for p in people
                             if all(k.startswith("mailto:") for k in p.keys))))
    say()

    say("channels (people reached by each)")
    for channel in ("mail", "imessage", "call", "calendar", "coreduet"):
        n = sum(1 for p in people if channel in p.channels)
        if n:
            say("  %-24s %6d" % (channel, n))
    say()

    say("reciprocity")
    bands = [("balanced  >=0.75", 0.75, 1.01), ("two-way   0.45-0.75", 0.45, 0.75),
             ("lopsided  0.15-0.45", 0.15, 0.45), ("one-sided  <0.15", -0.01, 0.15)]
    for label, low, high in bands:
        say("  %-24s %6d" % (label, sum(1 for p in people
                                        if low <= p.reciprocity < high)))
    say()

    say("activity")
    days = [len(p.days) for p in people]
    say("  %-24s %6d" % ("median days in contact", int(percentile(days, 0.5))))
    say("  %-24s %6d" % ("people with a meeting", sum(1 for p in people if p.n_meet)))
    say("  %-24s %6d" % ("people with a call", sum(1 for p in people if p.n_call)))

    print("\n".join(out))
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
    r.add_argument("--keep-broadcasters", action="store_true",
                   help="do not filter senders you never write back to")
    r.add_argument("--duplicates", action="store_true",
                   help="list people who appear twice under different addresses")
    r.add_argument("--fuse-by-name", action="store_true",
                   help="merge those duplicates instead of reporting them")
    r.add_argument("--out", metavar="FILE.csv", help="write the full ranking as CSV")
    r.add_argument("--json", metavar="FILE.json", help="write the full ranking as JSON")
    r.add_argument("--quiet", action="store_true", help="suppress the per-source summary")
    r.set_defaults(func=cmd_rank)

    d = sub.add_parser(
        "stats",
        help="diagnostic report with no personal data in it -- safe to share")
    d.add_argument("--since", type=int, default=DEFAULT_WINDOW_DAYS, metavar="DAYS")
    d.add_argument("--half-life", type=float, default=DEFAULT_HALF_LIFE_DAYS,
                   metavar="DAYS")
    d.add_argument("--min-active-days", type=int, default=3, metavar="N")
    d.add_argument("--sources", metavar="LIST")
    d.add_argument("--include-coreduet", action="store_true")
    d.add_argument("--keep-role-addresses", action="store_true")
    d.add_argument("--keep-broadcasters", action="store_true")
    d.set_defaults(func=cmd_stats)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
