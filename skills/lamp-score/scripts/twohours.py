#!/usr/bin/env python3
"""Two Hours data tools.

Creates, validates, and safely writes the job-search data files described in
schema/CONTRACT.md. Every skill in this repo uses these commands instead of
editing the files directly, so the contract's rules hold no matter which agent
or model is doing the work.

Standard library only. Python 3.9 or newer.

    python3 twohours.py --help
    python3 twohours.py <command> --help
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

CONTRACT_VERSION = "0.3"
TOOL_VERSION = "0.2.0"
DATA_DIR_ENV = "TWO_HOURS_DATA"

LAMP_FILE = "lamp.csv"
CONTACTS_FILE = "contacts.csv"
LOG_FILE = "outreach-log.csv"
SETTINGS_FILE = "job-search.json"
LOCK_FILE = ".lock"

LAMP_COLUMNS = [
    "id", "employer", "what", "bucket", "alumni", "motivation", "posting", "stage",
    "hold", "notes", "source", "alumni_checked", "posting_url", "posting_checked",
]
CONTACT_COLUMNS = [
    "id", "employer_id", "name", "title", "affinity", "connection", "mutuals",
    "source", "profile_url", "email", "hold", "notes",
]
LOG_COLUMNS = [
    "event_id", "at", "contact_id", "employer_id", "event", "kind", "channel",
    "draft", "actor", "ref", "note",
]
TABLES = (
    ("lamp", LAMP_FILE, LAMP_COLUMNS),
    ("contacts", CONTACTS_FILE, CONTACT_COLUMNS),
    ("log", LOG_FILE, LOG_COLUMNS),
)

BUCKETS = ("dream", "alumni", "hiring", "trending")
STAGES = ("hold", "meeting", "engaged", "contacting", "scored", "listed")
EVENTS = (
    "drafted", "approved", "sent", "replied", "meeting_scheduled", "meeting_held",
    "declined", "closed", "void",
)
KINDS = ("initial", "follow_up", "thank_you", "other")
CHANNELS = ("email", "linkedin", "other")
WRITERS = ("human", "lamp-list", "alumni-check", "lamp-score", "outreach-draft", "follow-up")
DRAFTERS = ("outreach-draft", "follow-up")
LAMP_SOURCES = ("user", "suggested", "job-board", "connections-export")
CONTACT_SOURCES = ("connections-export", "search", "referral", "user")
ALUMNI_CONTACT_SOURCES = ("connections-export", "search")

# Which writers may set each column (CONTRACT.md sections 3 and 4).
# `id` and `stage` are never set directly. Columns not listed here are
# user-added columns, which only a person may edit.
LAMP_OWNERS = {
    "employer": {"lamp-list", "human"},
    "what": {"lamp-list"},
    "bucket": {"lamp-list"},
    "alumni": {"alumni-check"},
    "motivation": {"human"},
    "posting": {"lamp-score"},
    "hold": {"human"},
    "notes": {"human"},
    "source": {"lamp-list"},
    "alumni_checked": {"alumni-check"},
    "posting_url": {"lamp-score"},
    "posting_checked": {"lamp-score"},
}
CONTACT_OWNERS = {
    "employer_id": {"alumni-check", "human"},
    "name": {"alumni-check", "human"},
    "title": {"alumni-check", "human"},
    "affinity": {"alumni-check", "human"},
    "connection": {"alumni-check", "human"},
    "mutuals": {"alumni-check", "human"},
    "source": {"alumni-check", "human"},
    "profile_url": {"alumni-check", "human"},
    "email": {"human"},
    "hold": {"human"},
    "notes": {"human"},
}
# Human-owned lamp columns accepted back from a spreadsheet (CONTRACT.md section 10).
IMPORT_COLUMNS = ("employer", "motivation", "hold", "notes")

REQUIRED = {
    "lamp": ("id", "employer"),
    "contacts": ("id", "employer_id", "name"),
    "log": ("event_id", "at", "contact_id", "employer_id", "event", "actor"),
}

RULES = {
    "lamp": {
        "id": ("regex", r"[a-z0-9-]+", "must use only lowercase letters, digits, and hyphens"),
        "bucket": ("enum", BUCKETS),
        "alumni": ("enum", ("Y", "N")),
        "motivation": ("enum", ("3", "2", "1", "0")),
        "posting": ("enum", ("3", "2", "1")),
        "stage": ("enum", STAGES),
        "hold": ("enum", ("Y",)),
        "alumni_checked": ("date",),
        "posting_checked": ("date",),
        "posting_url": ("url",),
    },
    "contacts": {
        "id": ("regex", r"c\d+", "must be c followed by digits, like c0007"),
        "connection": ("enum", ("1", "2", "3", "0")),
        "mutuals": ("int",),
        "hold": ("enum", ("Y",)),
        "profile_url": ("url",),
    },
    "log": {
        "event_id": ("regex", r"ev\d+", "must be ev followed by digits, like ev0012"),
        "at": ("timestamp",),
        "event": ("enum", EVENTS),
        "kind": ("enum", KINDS),
        "channel": ("enum", CHANNELS),
        "ref": ("regex", r"ev\d+", "must be an event_id, like ev0012"),
    },
}

DEFAULT_SETTINGS = {
    "contract_version": CONTRACT_VERSION,
    "timezone": "UTC",
    "list_target": 40,
    "buckets": list(BUCKETS),
    "affinities": [],
    "sort": ["motivation desc", "posting desc", "alumni desc", "employer asc"],
    "target_roles": [],
    "target_locations": [],
    "remote_ok": False,
    "posting_max_age_days": 7,
    "outreach_batch_size": 5,
    "follow_up": {
        "try_another_contact_after_business_days": 3,
        "nudge_same_contact_after_business_days": 7,
    },
}


# ---------------------------------------------------------------- errors

class DataError(Exception):
    """The data or the requested change breaks the contract. Nothing was written."""

    def __init__(self, errors):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("\n".join(self.errors))


class ConfigError(Exception):
    """Setup problem: missing data folder, bad settings, unsupported contract version."""


class LockTimeout(Exception):
    """Another writer held the lock for too long."""


# ---------------------------------------------------------------- files

class Table:
    def __init__(self, path, header, rows, lines, newline):
        self.path = path
        self.header = header
        self.rows = rows
        self.lines = lines
        self.newline = newline

    @property
    def name(self):
        return os.path.basename(self.path)

    def index(self, key="id"):
        return {r.get(key, "").strip(): r for r in self.rows if r.get(key, "").strip()}


def read_table(path, required_columns):
    name = os.path.basename(path)
    if not os.path.exists(path):
        raise DataError(f"{name} is missing. Run `init` to create it.")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    reader = csv.reader(io.StringIO(raw, newline=""))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise DataError(f"{name} is empty. The first row must be the header row.")
    except csv.Error as e:
        raise DataError(f"{name} is not readable as CSV ({e}).")
    missing = [c for c in required_columns if c not in header]
    if missing:
        raise DataError(
            f"{name}: missing column(s): {', '.join(missing)}. The header must be the first row; "
            "a legend or notes above it will cause this."
        )
    if "" in header:
        raise DataError(f"{name}: a header cell is blank. Every column needs a name.")
    dups = sorted({h for h in header if header.count(h) > 1})
    if dups:
        raise DataError(f"{name}: duplicate column name(s): {', '.join(dups)}.")
    rows, lines = [], []
    try:
        for rec in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in rec):
                continue
            if len(rec) > len(header):
                if any(cell.strip() for cell in rec[len(header):]):
                    raise DataError(
                        f"{name} line {line}: has {len(rec)} cells but the header has {len(header)} columns."
                    )
                rec = rec[: len(header)]
            rec = rec + [""] * (len(header) - len(rec))
            rows.append(dict(zip(header, rec)))
            lines.append(line)
    except csv.Error as e:
        raise DataError(f"{name} is not readable as CSV ({e}).")
    return Table(path, header, rows, lines, newline)


def _atomic_write(path, write_fn):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise


def write_table(table):
    def write(f):
        w = csv.writer(f, lineterminator=table.newline)
        w.writerow(table.header)
        for r in table.rows:
            w.writerow([r.get(c, "") for c in table.header])
    _atomic_write(table.path, write)


def write_text(path, text):
    _atomic_write(path, lambda f: f.write(text))


@contextmanager
def data_lock(data_dir, timeout=30.0, stale_after=60.0):
    """Hold <data_dir>/.lock for the duration of a read-modify-write."""
    path = os.path.join(data_dir, LOCK_FILE)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
            except FileNotFoundError:
                continue
            if age > stale_after:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Another writer is holding {path}. Try again shortly. If no other agent is "
                    f"running, the lock clears itself after {int(stale_after)} seconds."
                )
            time.sleep(0.2)
    try:
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
        os.close(fd)
        yield
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- settings

def resolve_data_dir(arg, must_exist=True):
    d = arg or os.environ.get(DATA_DIR_ENV)
    if not d:
        raise ConfigError(f"No data folder given. Pass --data-dir PATH or set {DATA_DIR_ENV}.")
    d = os.path.abspath(os.path.expanduser(d))
    if must_exist and not os.path.isdir(d):
        raise ConfigError(f"Data folder not found: {d}. Run `init --data-dir {d}` to create it.")
    return d


def check_contract_version(value):
    m = re.fullmatch(r"(\d+)\.(\d+)", str(value or ""))
    if not m:
        raise ConfigError(f'{SETTINGS_FILE}: contract_version must look like "{CONTRACT_VERSION}", got {value!r}.')
    if m.group(1) != CONTRACT_VERSION.split(".")[0]:
        raise ConfigError(
            f"{SETTINGS_FILE} uses data contract {value}, but these tools support {CONTRACT_VERSION}. "
            "The major versions differ, so the tools will not write. See CHANGELOG.md for how to migrate."
        )


def load_settings(data_dir):
    path = os.path.join(data_dir, SETTINGS_FILE)
    if not os.path.exists(path):
        raise ConfigError(f"{SETTINGS_FILE} not found in {data_dir}. Run `init` first.")
    try:
        with open(path, encoding="utf-8-sig") as f:
            settings = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{SETTINGS_FILE} is not valid JSON ({e}).")
    if not isinstance(settings, dict):
        raise ConfigError(f"{SETTINGS_FILE} must contain a JSON object.")
    check_contract_version(settings.get("contract_version"))
    return settings


def timezone_database_available():
    if ZoneInfo is None:
        return False
    try:
        ZoneInfo("America/New_York")
        return True
    except Exception:
        return False


def settings_timezone(settings):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(settings.get("timezone") or "UTC")
        except Exception:
            pass
    return None


def today_date(settings):
    tz = settings_timezone(settings)
    return (datetime.now(tz) if tz else datetime.now().astimezone()).date()


def check_target_settings(settings):
    """Type checks for the optional targeting keys added in contract 0.3."""
    errors = []
    for key in ("target_roles", "target_locations"):
        value = settings.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            errors.append(f"{SETTINGS_FILE}: {key} must be a list of text values")
    if not isinstance(settings.get("remote_ok", False), bool):
        errors.append(f"{SETTINGS_FILE}: remote_ok must be true or false")
    age = settings.get("posting_max_age_days", 7)
    if isinstance(age, bool) or not isinstance(age, int) or age < 1:
        errors.append(f"{SETTINGS_FILE}: posting_max_age_days must be a whole number of days, 1 or more")
    return errors


def now_timestamp(settings):
    name = settings.get("timezone") or "UTC"
    tz = None
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(name)
        except Exception:
            tz = None
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    return now.isoformat(timespec="seconds")


def enclosing_git_repo(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def git_warning(data_dir):
    repo = enclosing_git_repo(data_dir)
    if repo:
        return (
            f"{data_dir} is inside a git repository ({repo}). This data includes other people's "
            "details; keep the data folder outside any repository so it can never be committed."
        )
    return None


# ---------------------------------------------------------------- values

def parse_timestamp(value):
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(v)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def check_value(table_key, col, value):
    """Return an error message if `value` is not allowed in `col`, else None. Empty is always allowed here."""
    v = value.strip()
    if not v:
        return None
    rule = RULES.get(table_key, {}).get(col)
    if rule is None:
        return None
    kind = rule[0]
    if kind == "enum" and v not in rule[1]:
        return f"must be one of {', '.join(rule[1])}, or empty (got {v!r})"
    if kind == "regex" and not re.fullmatch(rule[1], v):
        return f"{rule[2]} (got {v!r})"
    if kind == "date":
        try:
            ok = len(v) == 10 and date.fromisoformat(v)
        except ValueError:
            ok = False
        if not ok:
            return f"must be a date like 2026-09-27 (got {v!r})"
    if kind == "int" and not v.isdigit():
        return f"must be a whole number (got {v!r})"
    if kind == "url" and not v.lower().startswith(("http://", "https://")):
        return f"must start with http:// or https:// (got {v!r})"
    if kind == "timestamp" and parse_timestamp(v) is None:
        return f"must be an ISO 8601 timestamp with a UTC offset, like 2026-09-27T10:30:00-07:00 (got {v!r})"
    return None


def normalize_name(name):
    return " ".join(name.casefold().split())


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "employer"


def unique_id(base, existing):
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def next_sequence(values, prefix, width=4):
    highest = 0
    for v in values:
        m = re.fullmatch(prefix + r"(\d+)", v.strip())
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}{highest + 1:0{width}d}"


def may_write(table_key, col, writer):
    if col in ("id", "stage"):
        return False
    owners = (LAMP_OWNERS if table_key == "lamp" else CONTACT_OWNERS).get(col)
    if owners is None:
        return writer == "human"
    return writer in owners


# ---------------------------------------------------------------- derived state

def live_events(log_rows):
    """Events that are not voids and have not been voided."""
    voided = {
        r.get("ref", "").strip() for r in log_rows
        if r.get("event", "").strip() == "void" and r.get("ref", "").strip()
    }
    return [
        r for r in log_rows
        if r.get("event", "").strip() != "void" and r.get("event_id", "").strip() not in voided
    ]


def compute_stage(lamp_row, employer_events):
    if lamp_row.get("hold", "").strip() == "Y":
        return "hold"
    kinds = {e.get("event", "").strip() for e in employer_events}
    if kinds & {"meeting_scheduled", "meeting_held"}:
        return "meeting"
    if "replied" in kinds:
        return "engaged"
    if kinds & {"drafted", "sent"}:
        return "contacting"
    if lamp_row.get("motivation", "").strip() and lamp_row.get("posting", "").strip():
        return "scored"
    return "listed"


def sync_stages(lamp, log):
    by_employer = {}
    for e in live_events(log.rows):
        by_employer.setdefault(e.get("employer_id", "").strip(), []).append(e)
    changes = []
    for r in lamp.rows:
        new = compute_stage(r, by_employer.get(r.get("id", "").strip(), []))
        old = r.get("stage", "").strip()
        if new != old:
            r["stage"] = new
            changes.append({"id": r["id"].strip(), "employer": r.get("employer", "").strip(), "from": old, "to": new})
    return changes


def contact_status(contact_id, log_rows):
    """(started, ended): a live `sent` exists / a live `declined` or `closed` exists."""
    events = [e for e in live_events(log_rows) if e.get("contact_id", "").strip() == contact_id]
    started = any(e.get("event", "").strip() == "sent" for e in events)
    ended = any(e.get("event", "").strip() in ("declined", "closed") for e in events)
    return started, ended


def load_all(data_dir):
    return {key: read_table(os.path.join(data_dir, fname), cols) for key, fname, cols in TABLES}


# ---------------------------------------------------------------- validate

def validate_data(data_dir):
    errors, warnings = [], []
    settings = None
    try:
        settings = load_settings(data_dir)
    except ConfigError as e:
        errors.append(str(e))

    tables = {}
    for key, fname, cols in TABLES:
        try:
            tables[key] = read_table(os.path.join(data_dir, fname), cols)
        except DataError as e:
            errors.extend(e.errors)
    lamp, contacts, log = tables.get("lamp"), tables.get("contacts"), tables.get("log")

    def where(t, i):
        return f"{t.name} line {t.lines[i]}"

    def check_rows(key, t, id_col):
        seen = {}
        for i, r in enumerate(t.rows):
            for col in REQUIRED[key]:
                if not r.get(col, "").strip():
                    errors.append(f"{where(t, i)}: {col} is required")
            for col in t.header:
                msg = check_value(key, col, r.get(col, ""))
                if msg:
                    errors.append(f"{where(t, i)}: {col} {msg}")
            rid = r.get(id_col, "").strip()
            if rid:
                if rid in seen:
                    errors.append(f"{where(t, i)}: duplicate {id_col} {rid!r} (first on line {seen[rid]})")
                else:
                    seen[rid] = t.lines[i]

    if settings is not None:
        sort = settings.get("sort", DEFAULT_SETTINGS["sort"])
        try:
            parse_sort(sort)
        except ConfigError as e:
            errors.append(str(e))
        for key in ("timezone", "affinities"):
            if key not in settings:
                warnings.append(f"{SETTINGS_FILE}: {key} is not set")
        errors.extend(check_target_settings(settings))
        if not settings.get("target_roles"):
            warnings.append(f"{SETTINGS_FILE}: target_roles is empty, so postings cannot be scored yet")

    if lamp:
        check_rows("lamp", lamp, "id")
        names = {}
        for i, r in enumerate(lamp.rows):
            if r.get("posting", "").strip() in ("2", "3") and not r.get("posting_url", "").strip():
                warnings.append(f"{where(lamp, i)}: posting is {r['posting'].strip()} but posting_url is empty")
            n = normalize_name(r.get("employer", ""))
            if n:
                if n in names:
                    warnings.append(f"{where(lamp, i)}: employer {r['employer'].strip()!r} is listed twice (also line {names[n]})")
                else:
                    names[n] = lamp.lines[i]

    if contacts:
        check_rows("contacts", contacts, "id")
        lamp_ids = lamp.index() if lamp else {}
        for i, r in enumerate(contacts.rows):
            emp = r.get("employer_id", "").strip()
            if lamp and emp and emp not in lamp_ids:
                errors.append(f"{where(contacts, i)}: employer_id {emp!r} is not in {LAMP_FILE}")
            src = r.get("source", "").strip()
            if src and src not in CONTACT_SOURCES:
                warnings.append(f"{where(contacts, i)}: source {src!r} is not one of {', '.join(CONTACT_SOURCES)}")

    if lamp and contacts:
        with_affinity = {
            c.get("employer_id", "").strip() for c in contacts.rows if c.get("affinity", "").strip()
        }
        for i, r in enumerate(lamp.rows):
            if r.get("alumni", "").strip() == "Y" and r.get("id", "").strip() not in with_affinity:
                warnings.append(f"{where(lamp, i)}: alumni is Y but no contact at this employer has an affinity")

    if log:
        check_rows("log", log, "event_id")
        contact_ids = contacts.index() if contacts else {}
        lamp_ids = lamp.index() if lamp else {}
        order = {}
        previous = 0
        for i, r in enumerate(log.rows):
            w = where(log, i)
            eid = r.get("event_id", "").strip()
            m = re.fullmatch(r"ev(\d+)", eid)
            if m:
                num = int(m.group(1))
                if num <= previous:
                    errors.append(f"{w}: event_id {eid} is out of order; the log is append-only and IDs must increase")
                previous = max(previous, num)
                order.setdefault(eid, i)
            event = r.get("event", "").strip()
            actor = r.get("actor", "").strip()
            cid = r.get("contact_id", "").strip()
            emp = r.get("employer_id", "").strip()
            ref = r.get("ref", "").strip()
            if contacts and cid and cid not in contact_ids:
                errors.append(f"{w}: contact_id {cid!r} is not in {CONTACTS_FILE}")
            if lamp and emp and emp not in lamp_ids:
                errors.append(f"{w}: employer_id {emp!r} is not in {LAMP_FILE}")
            if contacts and cid in contact_ids and emp:
                current = contact_ids[cid].get("employer_id", "").strip()
                if current and current != emp:
                    warnings.append(f"{w}: employer_id {emp!r} differs from {cid}'s current employer {current!r}")
            if event == "drafted":
                if actor and actor not in DRAFTERS:
                    errors.append(f"{w}: drafted events come from {' or '.join(DRAFTERS)}, not {actor!r}")
            elif event and actor and actor != "human":
                errors.append(f"{w}: {event} events record something only the user can confirm; actor must be human")
            if actor and actor not in WRITERS:
                warnings.append(f"{w}: actor {actor!r} is not a known skill or human")
            if event == "void":
                if not ref:
                    errors.append(f"{w}: void events need ref pointing at the event they cancel")
                elif ref not in order:
                    errors.append(f"{w}: ref {ref!r} is not an earlier event")
                elif log.rows[order[ref]].get("event", "").strip() == "void":
                    errors.append(f"{w}: ref {ref!r} is itself a void; voids cannot be voided")
            elif ref:
                errors.append(f"{w}: ref is only used on void events")
            draft = r.get("draft", "").strip()
            if draft:
                if os.path.isabs(draft) or os.path.normpath(draft).startswith(".."):
                    errors.append(f"{w}: draft must be a path inside the data folder, like drafts/{eid or 'ev0001'}.md")
                elif not os.path.exists(os.path.join(data_dir, draft)):
                    warnings.append(f"{w}: draft file {draft} not found")

        # Hold and ended-conversation checks, in log order.
        if contacts:
            live = {e.get("event_id", "").strip() for e in live_events(log.rows)}
            sent, ended = set(), set()
            for i, r in enumerate(log.rows):
                if r.get("event_id", "").strip() not in live:
                    continue
                event, cid = r.get("event", "").strip(), r.get("contact_id", "").strip()
                if event == "sent":
                    sent.add(cid)
                elif event in ("declined", "closed"):
                    ended.add(cid)
                elif event == "drafted":
                    if cid in ended:
                        warnings.append(f"{where(log, i)}: draft for {cid} after their conversation was declined or closed")
                    held = contact_ids.get(cid, {}).get("hold", "").strip() == "Y"
                    if held and cid not in sent:
                        warnings.append(
                            f"{where(log, i)}: draft for {cid}, who is on hold, before any message was sent "
                            "(fine if the hold was added after this draft)"
                        )

    if lamp and log:
        by_employer = {}
        for e in live_events(log.rows):
            by_employer.setdefault(e.get("employer_id", "").strip(), []).append(e)
        stale = [
            r["id"].strip() for r in lamp.rows
            if r.get("stage", "").strip() != compute_stage(r, by_employer.get(r.get("id", "").strip(), []))
        ]
        if stale:
            warnings.append(f"{LAMP_FILE}: stage is out of date for {len(stale)} row(s) ({', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}). Run `sync`.")

    warning = git_warning(data_dir)
    if warning:
        warnings.append(warning)
    return errors, warnings


# ---------------------------------------------------------------- ranking

NUMERIC_SORT = {"motivation", "posting"}


def parse_sort(spec):
    if not isinstance(spec, list) or not spec:
        raise ConfigError(f'{SETTINGS_FILE}: sort must be a list like ["motivation desc", "employer asc"].')
    parsed = []
    for item in spec:
        parts = str(item).split()
        if len(parts) == 1:
            parts.append("asc")
        if len(parts) != 2 or parts[1] not in ("asc", "desc"):
            raise ConfigError(f"{SETTINGS_FILE}: sort entry {item!r} must be a column name followed by asc or desc.")
        parsed.append((parts[0], parts[1]))
    return parsed


def rank_rows(rows, sort_spec):
    rows = list(rows)
    for col, direction in reversed(parse_sort(sort_spec)):
        desc = direction == "desc"
        if col == "alumni":
            weight = {"Y": 2, "N": 1}
            rows.sort(key=lambda r: weight.get(r.get("alumni", "").strip(), 0), reverse=desc)
            continue
        if col in NUMERIC_SORT:
            rows.sort(
                key=lambda r: int(r.get(col, "").strip()) if r.get(col, "").strip().isdigit() else 0,
                reverse=desc,
            )
        else:
            rows.sort(key=lambda r: r.get(col, "").strip().casefold(), reverse=desc)
        rows.sort(key=lambda r: 0 if r.get(col, "").strip() else 1)  # empty values last
    return rows


# ---------------------------------------------------------------- input items

def coerce(value, label, col):
    if value is None:
        return ""
    if isinstance(value, bool):
        raise DataError(f"{label}: {col} must be text or a number, not true/false")
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    raise DataError(f"{label}: {col} must be text or a number")


def read_items(args, need_id):
    if args.from_file and (args.set or getattr(args, "id", None)):
        raise ConfigError("Use either --from or --set/--id, not both.")
    if args.from_file:
        try:
            text = sys.stdin.read() if args.from_file == "-" else open(args.from_file, encoding="utf-8-sig").read()
            data = json.loads(text)
        except (OSError, json.JSONDecodeError) as e:
            raise ConfigError(f"Could not read JSON from {args.from_file}: {e}")
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
            raise ConfigError("--from must contain a JSON object or a list of objects.")
        raw_items = data
    else:
        if not args.set:
            raise ConfigError("Nothing to write. Use --set COLUMN=VALUE (repeatable) or --from FILE.")
        item = {}
        for pair in args.set:
            if "=" not in pair:
                raise ConfigError(f"--set {pair!r} must look like COLUMN=VALUE.")
            k, v = pair.split("=", 1)
            item[k.strip()] = v
        if need_id:
            if not args.id:
                raise ConfigError("--id is required with --set.")
            item["id"] = args.id
        raw_items = [item]
    items = []
    for n, raw in enumerate(raw_items, 1):
        label = f"item {n}"
        items.append({str(k).strip(): coerce(v, label, k) for k, v in raw.items()})
    return items


# ---------------------------------------------------------------- changes

def add_lamp_rows(lamp, items, writer):
    errors, added, skipped = [], [], []
    ids = {r["id"].strip() for r in lamp.rows}
    names = {normalize_name(r["employer"]): r["id"].strip() for r in lamp.rows if r["employer"].strip()}
    for n, item in enumerate(items, 1):
        label, item_errors = f"item {n}", []
        for col in ("id", "stage"):
            if col in item:
                item_errors.append(f"{label}: {col} is set automatically; leave it out")
        employer = item.get("employer", "")
        if not employer:
            item_errors.append(f"{label}: employer is required")
        for col, value in item.items():
            if col in ("id", "stage"):
                continue
            if col not in lamp.header:
                item_errors.append(f"{label}: unknown column {col!r}")
            elif not may_write("lamp", col, writer):
                item_errors.append(f"{label}: {writer} may not write {col}")
            else:
                msg = check_value("lamp", col, value)
                if msg:
                    item_errors.append(f"{label}: {col} {msg}")
        if writer == "lamp-list" and not item.get("source"):
            item_errors.append(f"{label}: source is required ({', '.join(LAMP_SOURCES)}, or another short label)")
        if item_errors:
            errors.extend(item_errors)
            continue
        key = normalize_name(employer)
        if key in names:
            skipped.append({"employer": employer, "existing_id": names[key], "reason": "already listed"})
            continue
        new_id = unique_id(slugify(employer), ids)
        row = {c: "" for c in lamp.header}
        row.update(item)
        row["id"] = new_id
        if writer == "human":
            row["source"] = "user"
        lamp.rows.append(row)
        ids.add(new_id)
        names[key] = new_id
        added.append({"id": new_id, "employer": employer})
    if errors:
        raise DataError(errors)
    return added, skipped


def add_contact_rows(contacts, lamp, items, writer):
    errors, added, skipped = [], [], []
    lamp_ids = lamp.index()
    ids = [r["id"] for r in contacts.rows]
    seen = {
        (r["employer_id"].strip(), normalize_name(r["name"])) for r in contacts.rows
    }
    for n, item in enumerate(items, 1):
        label, item_errors = f"item {n}", []
        if "id" in item:
            item_errors.append(f"{label}: id is set automatically; leave it out")
        for col, value in item.items():
            if col == "id":
                continue
            if col not in contacts.header:
                item_errors.append(f"{label}: unknown column {col!r}")
            elif not may_write("contacts", col, writer):
                item_errors.append(f"{label}: {writer} may not write {col}")
            else:
                msg = check_value("contacts", col, value)
                if msg:
                    item_errors.append(f"{label}: {col} {msg}")
        emp = item.get("employer_id", "")
        if not emp:
            item_errors.append(f"{label}: employer_id is required")
        elif emp not in lamp_ids:
            item_errors.append(f"{label}: employer_id {emp!r} is not in {LAMP_FILE}")
        if not item.get("name"):
            item_errors.append(f"{label}: name is required")
        source = item.get("source", "")
        if writer == "alumni-check" and source not in ALUMNI_CONTACT_SOURCES:
            item_errors.append(f"{label}: alumni-check must set source to {' or '.join(ALUMNI_CONTACT_SOURCES)}")
        if item_errors:
            errors.extend(item_errors)
            continue
        key = (emp, normalize_name(item["name"]))
        if key in seen:
            skipped.append({"name": item["name"], "employer_id": emp, "reason": "already a contact at this employer"})
            continue
        new_id = next_sequence(ids, "c")
        row = {c: "" for c in contacts.header}
        row.update(item)
        row["id"] = new_id
        if writer == "human" and not row.get("source"):
            row["source"] = "user"
        contacts.rows.append(row)
        ids.append(new_id)
        seen.add(key)
        added.append({"id": new_id, "name": item["name"], "employer_id": emp})
    if errors:
        raise DataError(errors)
    return added, skipped


def update_rows(table_key, table, items, writer, lamp=None):
    errors, pending = [], []
    index = table.index()
    lamp_ids = lamp.index() if lamp is not None else {}
    for n, item in enumerate(items, 1):
        item = dict(item)
        rid = item.pop("id", "")
        label = f"item {n} ({rid})" if rid else f"item {n}"
        if not rid:
            errors.append(f"{label}: id is required")
            continue
        row = index.get(rid)
        if row is None:
            errors.append(f"{label}: no row with id {rid!r} in {table.name}")
            continue
        if not item:
            errors.append(f"{label}: nothing to change")
            continue
        if table_key == "contacts" and writer == "alumni-check" and row.get("source", "").strip() not in ALUMNI_CONTACT_SOURCES:
            errors.append(f"{label}: alumni-check may only update contacts it found (source {' or '.join(ALUMNI_CONTACT_SOURCES)})")
            continue
        for col, value in item.items():
            if col not in table.header:
                errors.append(f"{label}: unknown column {col!r}")
            elif not may_write(table_key, col, writer):
                errors.append(f"{label}: {writer} may not write {col}")
            else:
                msg = check_value(table_key, col, value)
                if msg:
                    errors.append(f"{label}: {col} {msg}")
                elif col in REQUIRED[table_key] and not value:
                    errors.append(f"{label}: {col} cannot be empty")
                elif table_key == "contacts" and col == "employer_id" and value not in lamp_ids:
                    errors.append(f"{label}: employer_id {value!r} is not in {LAMP_FILE}")
                else:
                    pending.append((row, rid, col, value))
    if errors:
        raise DataError(errors)
    changes = []
    for row, rid, col, value in pending:
        old = row.get(col, "")
        if old.strip() != value:
            row[col] = value
            changes.append({"id": rid, "column": col, "from": old, "to": value})
    return changes


# ---------------------------------------------------------------- commands

def _result(**kw):
    kw.setdefault("ok", True)
    kw.setdefault("errors", [])
    kw.setdefault("warnings", [])
    return kw


def _stage_lines(changes):
    return [f"  stage {c['id']}: {c['from'] or '(empty)'} -> {c['to']}" for c in changes]


def cmd_init(args):
    d = resolve_data_dir(args.data_dir, must_exist=False)
    warnings = []
    tz = args.timezone
    if tz:
        if timezone_database_available():
            try:
                ZoneInfo(tz)
            except Exception:
                raise ConfigError(f"{tz!r} is not a known IANA time zone, like America/Los_Angeles.")
        else:
            warnings.append(
                f"This computer has no time zone database, so {tz!r} could not be checked. Timestamps will "
                "use the computer's local time. Installing the tzdata package (pip install tzdata) fixes this."
            )
    os.makedirs(d, exist_ok=True)
    created, kept = [], []
    for _, fname, cols in TABLES:
        path = os.path.join(d, fname)
        if os.path.exists(path):
            kept.append(fname)
        else:
            write_text(path, ",".join(cols) + "\n")
            created.append(fname)
    settings_path = os.path.join(d, SETTINGS_FILE)
    if os.path.exists(settings_path):
        kept.append(SETTINGS_FILE)
    else:
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        settings["timezone"] = tz or "UTC"
        settings["affinities"] = args.affinity or []
        settings["target_roles"] = args.target_role or []
        settings["target_locations"] = args.target_location or []
        settings["remote_ok"] = bool(args.remote_ok)
        write_text(settings_path, json.dumps(settings, indent=2) + "\n")
        created.append(SETTINGS_FILE)
        if not tz:
            warnings.append(f"timezone set to UTC. Edit {SETTINGS_FILE} or rerun init with --timezone.")
        if not args.affinity:
            warnings.append(f"no affinities yet. Add your schools and past employers to {SETTINGS_FILE}.")
        if not args.target_role:
            warnings.append(f"no target roles yet. Add the roles you want to {SETTINGS_FILE} before scoring postings.")
    for sub in ("drafts", "inputs", "reports"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    warning = git_warning(d)
    if warning:
        warnings.append(warning)
    lines = [f"Data folder: {d}"]
    lines += [f"  created {f}" for f in created] + [f"  kept existing {f}" for f in kept]
    return _result(data_dir=d, created=created, kept=kept, warnings=warnings), lines


def cmd_validate(args):
    d = resolve_data_dir(args.data_dir)
    errors, warnings = validate_data(d)
    ok = not errors
    lines = [f"{'Valid' if ok else 'Not valid'}: {len(errors)} error(s), {len(warnings)} warning(s)"]
    return _result(ok=ok, errors=errors, warnings=warnings), lines


def cmd_sync(args):
    d = resolve_data_dir(args.data_dir)
    load_settings(d)
    with data_lock(d):
        t = load_all(d)
        changes = sync_stages(t["lamp"], t["log"])
        if changes and not args.dry_run:
            write_table(t["lamp"])
    lines = [f"{len(changes)} stage change(s){' (dry run, nothing written)' if args.dry_run else ''}"] + _stage_lines(changes)
    return _result(stage_changes=changes, dry_run=args.dry_run), lines


def cmd_rank(args):
    d = resolve_data_dir(args.data_dir)
    settings = load_settings(d)
    lamp = read_table(os.path.join(d, LAMP_FILE), LAMP_COLUMNS)
    rows = [r for r in lamp.rows if args.include_hold or r.get("hold", "").strip() != "Y"]
    rows = rank_rows(rows, settings.get("sort", DEFAULT_SETTINGS["sort"]))
    if args.limit:
        rows = rows[: args.limit]
    out = [
        {k: r.get(k, "").strip() for k in ("id", "employer", "motivation", "posting", "alumni", "stage", "bucket")}
        for r in rows
    ]
    lines = [f"{'#':>3}  {'M':1} {'P':1} {'A':1}  {'stage':<11} employer"]
    for i, r in enumerate(out, 1):
        lines.append(
            f"{i:>3}  {r['motivation'] or '-':1} {r['posting'] or '-':1} {r['alumni'] or '-':1}  {r['stage'] or '-':<11} {r['employer']}"
        )
    return _result(rows=out), lines


def cmd_add(args):
    d = resolve_data_dir(args.data_dir)
    load_settings(d)
    items = read_items(args, need_id=False)
    with data_lock(d):
        t = load_all(d)
        if args.table == "lamp":
            added, skipped = add_lamp_rows(t["lamp"], items, args.writer)
        else:
            added, skipped = add_contact_rows(t["contacts"], t["lamp"], items, args.writer)
        stage_changes = sync_stages(t["lamp"], t["log"])
        if not args.dry_run:
            if args.table == "contacts" and added:
                write_table(t["contacts"])
            if (args.table == "lamp" and added) or stage_changes:
                write_table(t["lamp"])
    lines = [f"Added {len(added)}, skipped {len(skipped)}{' (dry run, nothing written)' if args.dry_run else ''}"]
    lines += [f"  + {a['id']}  {a.get('employer') or a.get('name')}" for a in added]
    lines += [f"  = {s.get('employer') or s.get('name')}: {s['reason']}" for s in skipped]
    lines += _stage_lines(stage_changes)
    return _result(added=added, skipped=skipped, stage_changes=stage_changes, dry_run=args.dry_run), lines


def cmd_update(args):
    d = resolve_data_dir(args.data_dir)
    load_settings(d)
    items = read_items(args, need_id=True)
    with data_lock(d):
        t = load_all(d)
        table = t["lamp"] if args.table == "lamp" else t["contacts"]
        changes = update_rows(args.table, table, items, args.writer, lamp=t["lamp"])
        stage_changes = sync_stages(t["lamp"], t["log"])
        if not args.dry_run:
            if args.table == "contacts" and changes:
                write_table(t["contacts"])
            if (args.table == "lamp" and changes) or stage_changes:
                write_table(t["lamp"])
    lines = [f"Changed {len(changes)} value(s){' (dry run, nothing written)' if args.dry_run else ''}"]
    lines += [f"  {c['id']}.{c['column']}: {c['from'] or '(empty)'} -> {c['to'] or '(empty)'}" for c in changes]
    lines += _stage_lines(stage_changes)
    return _result(changes=changes, stage_changes=stage_changes, dry_run=args.dry_run), lines


def cmd_log(args):
    d = resolve_data_dir(args.data_dir)
    settings = load_settings(d)
    event, writer = args.event, args.writer
    if args.draft and args.draft_from:
        raise ConfigError("Use either --draft or --draft-from, not both.")
    draft_text = None
    if args.draft_from:
        try:
            draft_text = sys.stdin.read() if args.draft_from == "-" else open(args.draft_from, encoding="utf-8").read()
        except OSError as e:
            raise ConfigError(f"Could not read draft from {args.draft_from}: {e}")
    with data_lock(d):
        t = load_all(d)
        lamp, contacts, log = t["lamp"], t["contacts"], t["log"]
        errors = []
        if event == "drafted":
            if writer not in DRAFTERS:
                errors.append(f"Only {' or '.join(DRAFTERS)} may log drafted events.")
        elif writer != "human":
            errors.append(
                f"A {event} event records something only the user can confirm. "
                "Log it with --as human, and only after the user says it happened."
            )
        contact_index, events = contacts.index(), log.index("event_id")
        ref = (args.ref or "").strip()
        contact_id = (args.contact or "").strip()
        if event == "void":
            target = events.get(ref) if ref else None
            if not ref:
                errors.append("A void event needs --ref EVENT_ID.")
            elif target is None:
                errors.append(f"No event {ref!r} in {LOG_FILE}.")
            elif target.get("event", "").strip() == "void":
                errors.append(f"{ref} is itself a void; voids cannot be voided.")
            elif ref not in {e["event_id"].strip() for e in live_events(log.rows)}:
                errors.append(f"{ref} is already voided.")
            else:
                contact_id = contact_id or target.get("contact_id", "").strip()
        elif ref:
            errors.append("--ref is only used with --event void.")
        contact = contact_index.get(contact_id)
        if not contact_id:
            errors.append("--contact is required.")
        elif contact is None:
            errors.append(f"No contact {contact_id!r} in {CONTACTS_FILE}.")
        for col, value in (("kind", args.kind), ("channel", args.channel)):
            msg = check_value("log", col, value or "")
            if msg:
                errors.append(f"--{col} {msg}")
        if event in ("drafted", "sent") and not args.kind:
            errors.append(f"--kind is required for {event} events ({', '.join(KINDS)}).")
        at = args.at or now_timestamp(settings)
        msg = check_value("log", "at", at)
        if msg:
            errors.append(f"--at {msg}")
        if args.draft:
            p = args.draft.strip()
            if os.path.isabs(p) or os.path.normpath(p).startswith(".."):
                errors.append("--draft must be a path inside the data folder, like drafts/ev0012.md.")
        if event == "drafted" and contact is not None:
            started, ended = contact_status(contact_id, log.rows)
            if ended:
                errors.append(f"{contact_id}'s conversation was declined or closed, so no new drafts.")
            elif contact.get("hold", "").strip() == "Y" and not started:
                errors.append(
                    f"{contact_id} is on hold and no message has been sent to them yet. Hold blocks new "
                    "outreach: pick another contact, or ask the user to clear the hold."
                )
        if errors:
            raise DataError(errors)

        event_id = next_sequence([r["event_id"] for r in log.rows], "ev")
        draft_path = args.draft.strip() if args.draft else ""
        if draft_text is not None:
            draft_path = f"drafts/{event_id}.md"
        row = {c: "" for c in log.header}
        row.update({
            "event_id": event_id,
            "at": at,
            "contact_id": contact_id,
            "employer_id": contact.get("employer_id", "").strip(),
            "event": event,
            "kind": args.kind or "",
            "channel": args.channel or "",
            "draft": draft_path,
            "actor": writer,
            "ref": ref,
            "note": (args.note or "").strip(),
        })
        log.rows.append(row)
        stage_changes = sync_stages(lamp, log)
        if not args.dry_run:
            if draft_text is not None:
                os.makedirs(os.path.join(d, "drafts"), exist_ok=True)
                write_text(os.path.join(d, draft_path), draft_text)
            write_table(log)
            if stage_changes:
                write_table(lamp)
    lines = [f"Logged {event_id}: {event} for {contact_id}{' (dry run, nothing written)' if args.dry_run else ''}"]
    if draft_path:
        lines.append(f"  draft: {draft_path}")
    lines += _stage_lines(stage_changes)
    return _result(event=row, stage_changes=stage_changes, dry_run=args.dry_run), lines


def cmd_import(args):
    d = resolve_data_dir(args.data_dir)
    load_settings(d)
    sheet = read_table(os.path.abspath(args.file), ["employer"])
    if "id" not in sheet.header:
        raise DataError(
            "The spreadsheet has no id column, so its rows cannot be matched to lamp.csv. "
            "Keep the id column when you use lamp.csv in a spreadsheet (hiding it is fine)."
        )
    warnings = []
    with data_lock(d):
        t = load_all(d)
        lamp, log = t["lamp"], t["log"]
        index = lamp.index()
        names = {normalize_name(r["employer"]): r["id"].strip() for r in lamp.rows if r["employer"].strip()}
        ids = set(index)
        new_columns = [c for c in sheet.header if c not in lamp.header]
        errors, pending, new_rows, ignored = [], [], [], set()
        seen_ids = set()

        def human_column(col):
            return col in IMPORT_COLUMNS or col not in LAMP_COLUMNS

        for i, srow in enumerate(sheet.rows):
            where = f"{sheet.name} line {sheet.lines[i]}"
            sid = srow.get("id", "").strip()
            if sid:
                seen_ids.add(sid)
                row = index.get(sid)
                if row is None:
                    warnings.append(f"{where}: id {sid!r} is not in {LAMP_FILE}; row skipped")
                    continue
                for col in sheet.header:
                    if col == "id":
                        continue
                    value = srow.get(col, "")
                    if value.strip() == row.get(col, "").strip():
                        continue
                    if not human_column(col):
                        ignored.add(col)
                        continue
                    msg = check_value("lamp", col, value)
                    if msg:
                        errors.append(f"{where}: {col} {msg}")
                    elif col == "employer" and not value.strip():
                        errors.append(f"{where}: employer cannot be blank")
                    else:
                        pending.append((row, sid, col, value.strip() if col in IMPORT_COLUMNS else value))
            else:
                employer = srow.get("employer", "").strip()
                if not employer:
                    continue
                key = normalize_name(employer)
                if key in names:
                    warnings.append(f"{where}: {employer!r} is already listed as {names[key]}; row skipped")
                    continue
                row = {}
                for col in sheet.header:
                    value = srow.get(col, "")
                    if col == "id" or not value.strip():
                        continue
                    if not human_column(col):
                        ignored.add(col)
                        continue
                    msg = check_value("lamp", col, value)
                    if msg:
                        errors.append(f"{where}: {col} {msg}")
                    row[col] = value.strip() if col in IMPORT_COLUMNS else value
                new_id = unique_id(slugify(employer), ids)
                ids.add(new_id)
                names[key] = new_id
                row["id"], row["source"] = new_id, "user"
                new_rows.append(row)
        if errors:
            raise DataError(errors)

        for col in new_columns:
            lamp.header.append(col)
            for r in lamp.rows:
                r.setdefault(col, "")
        changes = []
        for row, sid, col, value in pending:
            changes.append({"id": sid, "column": col, "from": row.get(col, ""), "to": value})
            row[col] = value
        added = []
        for partial in new_rows:
            row = {c: "" for c in lamp.header}
            row.update(partial)
            lamp.rows.append(row)
            added.append({"id": row["id"], "employer": row["employer"]})
        stage_changes = sync_stages(lamp, log)
        untouched = len([r for r in index if r not in seen_ids])
        if not args.dry_run and (changes or added or new_columns or stage_changes):
            write_table(lamp)
    if ignored:
        warnings.append(
            f"Ignored edits to columns the skills own: {', '.join(sorted(ignored))}. "
            f"{LAMP_FILE} stays the source of truth for those."
        )
    lines = [
        f"Updated {len(changes)} value(s), added {len(added)} employer(s), "
        f"{untouched} row(s) not in the sheet left unchanged{' (dry run, nothing written)' if args.dry_run else ''}"
    ]
    lines += [f"  {c['id']}.{c['column']}: {c['from'] or '(empty)'} -> {c['to'] or '(empty)'}" for c in changes]
    lines += [f"  + {a['id']}  {a['employer']}" for a in added]
    lines += [f"  new column: {c}" for c in new_columns]
    lines += _stage_lines(stage_changes)
    return _result(
        changes=changes, added=added, new_columns=new_columns, ignored_columns=sorted(ignored),
        stage_changes=stage_changes, warnings=warnings, dry_run=args.dry_run,
    ), lines


def cmd_queue(args):
    d = resolve_data_dir(args.data_dir)
    settings = load_settings(d)
    errors = check_target_settings(settings)
    if errors:
        raise ConfigError(" ".join(errors))
    lamp = read_table(os.path.join(d, LAMP_FILE), LAMP_COLUMNS)
    max_age = settings.get("posting_max_age_days", DEFAULT_SETTINGS["posting_max_age_days"])
    today = today_date(settings)
    due = []
    for r in lamp.rows:
        if r.get("hold", "").strip() == "Y":
            continue
        checked = r.get("posting_checked", "").strip()
        if not r.get("posting", "").strip() or not checked:
            reason = "never checked"
        else:
            try:
                age = (today - date.fromisoformat(checked)).days
            except ValueError:
                age = None
            if age is None:
                reason = "check date unreadable"
            elif args.all or age >= max_age:
                reason = f"checked {age} day(s) ago"
            else:
                continue
        due.append((r, reason))
    ranked = rank_rows([r for r, _ in due], settings.get("sort", DEFAULT_SETTINGS["sort"]))
    reasons = {id(r): reason for r, reason in due}
    if args.limit:
        ranked = ranked[: args.limit]
    keys = ("id", "employer", "what", "motivation", "posting", "posting_url", "posting_checked")
    out = [dict({k: r.get(k, "").strip() for k in keys}, reason=reasons[id(r)]) for r in ranked]
    targets = {k: settings.get(k, DEFAULT_SETTINGS[k]) for k in ("target_roles", "target_locations", "remote_ok")}
    warnings = [] if targets["target_roles"] else [
        f"target_roles is empty in {SETTINGS_FILE}. Ask the user which roles fit before scoring."
    ]
    lines = [f"{len(out)} employer(s) due for a posting check (older than {max_age} days or never checked)"]
    lines += [f"  {r['motivation'] or '-'}  {r['employer']}  ({r['reason']})" for r in out]
    return _result(queue="posting", today=today.isoformat(), max_age_days=max_age, targets=targets,
                   rows=out, warnings=warnings), lines


COMMANDS = {
    "init": cmd_init,
    "validate": cmd_validate,
    "sync": cmd_sync,
    "rank": cmd_rank,
    "queue": cmd_queue,
    "add": cmd_add,
    "update": cmd_update,
    "log": cmd_log,
    "import": cmd_import,
}


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="twohours.py",
        description="Create, check, and safely update Two Hours job-search data (see schema/CONTRACT.md).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION} (data contract {CONTRACT_VERSION})")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", metavar="PATH", help=f"data folder (default: ${DATA_DIR_ENV})")
    common.add_argument("--json", action="store_true", help="print a machine-readable JSON result")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("init", parents=[common], help="create a data folder with empty files and settings")
    s.add_argument("--timezone", help="IANA time zone, like America/Los_Angeles")
    s.add_argument("--affinity", action="append", metavar="NAME", help="a school or past employer (repeatable)")
    s.add_argument("--target-role", action="append", metavar="ROLE", help="a role you want (repeatable)")
    s.add_argument("--target-location", action="append", metavar="PLACE", help="a place you can work (repeatable)")
    s.add_argument("--remote-ok", action="store_true", help="remote roles fit")

    sub.add_parser("validate", parents=[common], help="check every file against the contract; exits 1 on errors")

    s = sub.add_parser("sync", parents=[common], help="recalculate the stage column")
    s.add_argument("--dry-run", action="store_true", help="show changes without writing")

    s = sub.add_parser("rank", parents=[common], help="print the ranked employer list")
    s.add_argument("--limit", type=int, help="show only the top N")
    s.add_argument("--include-hold", action="store_true", help="include employers on hold")

    s = sub.add_parser("queue", parents=[common], help="list employers due for a check, highest ranked first")
    s.add_argument("kind", choices=("posting",), help="which check")
    s.add_argument("--all", action="store_true", help="include employers checked recently")
    s.add_argument("--limit", type=int, help="show only the first N")

    for name, text in (("add", "add rows to lamp.csv or contacts.csv"), ("update", "change values in existing rows")):
        s = sub.add_parser(name, parents=[common], help=text)
        s.add_argument("table", choices=("lamp", "contacts"))
        s.add_argument("--as", dest="writer", required=True, choices=WRITERS, help="who is writing (enforces column ownership)")
        s.add_argument("--set", action="append", metavar="COLUMN=VALUE", help="a value to write (repeatable)")
        if name == "update":
            s.add_argument("--id", help="row to change, when using --set")
        s.add_argument("--from", dest="from_file", metavar="FILE", help="JSON object or list of objects; - reads stdin")
        s.add_argument("--dry-run", action="store_true", help="check and show changes without writing")

    s = sub.add_parser("log", parents=[common], help="append an event to outreach-log.csv")
    s.add_argument("--as", dest="writer", required=True, choices=WRITERS, help="who is logging")
    s.add_argument("--event", required=True, choices=EVENTS)
    s.add_argument("--contact", metavar="CONTACT_ID", help="contact id, like c0007 (optional for void)")
    s.add_argument("--kind", choices=KINDS, help="what the message is for (required for drafted and sent)")
    s.add_argument("--channel", choices=CHANNELS)
    s.add_argument("--draft", metavar="PATH", help="draft file already in the data folder, like drafts/ev0012.md")
    s.add_argument("--draft-from", metavar="FILE", help="save this text as drafts/<event_id>.md; - reads stdin")
    s.add_argument("--at", metavar="TIMESTAMP", help="when it happened (default: now, in the settings time zone)")
    s.add_argument("--ref", metavar="EVENT_ID", help="event to cancel (void only)")
    s.add_argument("--note")
    s.add_argument("--dry-run", action="store_true", help="check without writing")

    s = sub.add_parser("import", parents=[common], help="merge a spreadsheet export of lamp.csv back in")
    s.add_argument("file", help="CSV exported from the spreadsheet")
    s.add_argument("--dry-run", action="store_true", help="show changes without writing")
    return p


def emit(result, lines, as_json):
    if as_json:
        print(json.dumps(result, indent=2))
        return
    for line in lines:
        print(line)
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr)
    for e in result.get("errors", []):
        print(f"error: {e}", file=sys.stderr)


def main(argv=None):
    args = build_parser().parse_args(argv)
    lines = []
    try:
        result, lines = COMMANDS[args.command](args)
        code = 0 if result.get("ok", True) else 1
    except DataError as e:
        result, code = _result(ok=False, errors=e.errors), 1
        lines = ["Nothing was written."]
    except ConfigError as e:
        result, code = _result(ok=False, errors=[str(e)]), 2
    except LockTimeout as e:
        result, code = _result(ok=False, errors=[str(e)]), 3
    emit(result, lines, getattr(args, "json", False))
    return code


if __name__ == "__main__":
    sys.exit(main())
