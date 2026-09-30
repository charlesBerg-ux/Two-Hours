"""Tests for scripts/twohours.py. Run from the repo root:

    python3 -m unittest discover -s tests -v
"""

import csv
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "schema", "examples")

_spec = importlib.util.spec_from_file_location("twohours", os.path.join(ROOT, "scripts", "twohours.py"))
th = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(th)


def read(path, mode="r", **kw):
    with open(path, mode, **kw) as f:
        return f.read()


def write(path, text, **kw):
    with open(path, "w", **kw) as f:
        f.write(text)


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="two-hours-test-")
        self.d = os.path.join(self.tmp, "data")
        os.makedirs(self.d)
        for name in ("lamp", "contacts", "outreach-log"):
            shutil.copy(os.path.join(EXAMPLES, f"{name}.example.csv"), os.path.join(self.d, f"{name}.csv"))
        shutil.copy(os.path.join(ROOT, "schema", "job-search.example.json"), os.path.join(self.d, "job-search.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = th.main(list(argv) + ["--data-dir", self.d, "--json"])
        finally:
            sys.stdin = old_stdin
        return code, json.loads(out.getvalue())

    def lamp(self):
        return {r["id"]: r for r in rows(os.path.join(self.d, "lamp.csv"))}

    def contacts(self):
        return {r["id"]: r for r in rows(os.path.join(self.d, "contacts.csv"))}

    def log(self):
        return rows(os.path.join(self.d, "outreach-log.csv"))


class TestExamplesAndInit(Base):
    def test_examples_are_valid(self):
        code, res = self.run_cli("validate")
        self.assertEqual(code, 0, res["errors"])
        self.assertEqual(res["errors"], [])

    def test_examples_stages_are_current(self):
        code, res = self.run_cli("sync")
        self.assertEqual(code, 0)
        self.assertEqual(res["stage_changes"], [])

    def test_template_headers_match_tool_columns(self):
        for fname, cols in (("lamp.csv", th.LAMP_COLUMNS), ("contacts.csv", th.CONTACT_COLUMNS),
                            ("outreach-log.csv", th.LOG_COLUMNS)):
            with open(os.path.join(ROOT, "schema", fname), newline="") as f:
                self.assertEqual(next(csv.reader(f)), cols, fname)

    def test_init_creates_a_valid_empty_folder(self):
        new = os.path.join(self.tmp, "fresh")
        out = io.StringIO()
        with redirect_stdout(out):
            code = th.main(["init", "--data-dir", new, "--timezone", "America/Los_Angeles",
                            "--affinity", "Example Corp", "--json"])
        self.assertEqual(code, 0)
        for f in ("lamp.csv", "contacts.csv", "outreach-log.csv", "job-search.json"):
            self.assertTrue(os.path.exists(os.path.join(new, f)))
        settings = json.loads(read(os.path.join(new, "job-search.json")))
        self.assertEqual(settings["timezone"], "America/Los_Angeles")
        self.assertEqual(settings["affinities"], ["Example Corp"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(th.main(["validate", "--data-dir", new]), 0)

    def test_init_keeps_existing_files(self):
        before = read(os.path.join(self.d, "lamp.csv"))
        code, res = self.run_cli("init")
        self.assertEqual(code, 0)
        self.assertIn("lamp.csv", res["kept"])
        self.assertEqual(read(os.path.join(self.d, "lamp.csv")), before)


class TestStages(Base):
    def test_stage_follows_the_rules(self):
        code, res = self.run_cli("add", "lamp", "--as", "human", "--set", "employer=Acme Solar")
        self.assertEqual(code, 0, res)
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "listed")
        self.assertEqual(self.lamp()["acme-solar"]["source"], "user")

        self.run_cli("update", "lamp", "--as", "human", "--id", "acme-solar", "--set", "motivation=2")
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "listed")
        self.run_cli("update", "lamp", "--as", "lamp-score", "--id", "acme-solar", "--set", "posting=1")
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "scored")

        code, res = self.run_cli("add", "contacts", "--as", "human",
                                 "--set", "employer_id=acme-solar", "--set", "name=Pat Kim")
        self.assertEqual(code, 0, res)
        cid = res["added"][0]["id"]
        self.assertEqual(cid, "c0004")

        self.run_cli("log", "--as", "outreach-draft", "--event", "drafted", "--contact", cid, "--kind", "initial")
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "contacting")
        self.run_cli("log", "--as", "human", "--event", "sent", "--contact", cid, "--kind", "initial")
        self.run_cli("log", "--as", "human", "--event", "replied", "--contact", cid)
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "engaged")
        code, res = self.run_cli("log", "--as", "human", "--event", "meeting_scheduled", "--contact", cid)
        meeting_id = res["event"]["event_id"]
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "meeting")

        code, res = self.run_cli("log", "--as", "human", "--event", "void", "--ref", meeting_id)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["event"]["contact_id"], cid)
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "engaged")

        self.run_cli("update", "lamp", "--as", "human", "--id", "acme-solar", "--set", "hold=Y")
        self.assertEqual(self.lamp()["acme-solar"]["stage"], "hold")

    def test_void_rules(self):
        code, res = self.run_cli("log", "--as", "human", "--event", "void", "--ref", "ev0002")
        self.assertEqual(code, 0, res)
        void_id = res["event"]["event_id"]
        self.assertEqual(self.run_cli("log", "--as", "human", "--event", "void", "--ref", "ev0002")[0], 1)
        self.assertEqual(self.run_cli("log", "--as", "human", "--event", "void", "--ref", void_id)[0], 1)
        self.assertEqual(self.run_cli("log", "--as", "human", "--event", "void", "--ref", "ev9999")[0], 1)
        self.assertEqual(self.run_cli("log", "--as", "human", "--event", "sent", "--contact", "c0001",
                                      "--kind", "initial", "--ref", "ev0001")[0], 1)


class TestOwnership(Base):
    def test_writers_can_only_write_their_columns(self):
        before = read(os.path.join(self.d, "lamp.csv"))
        cases = [
            ("alumni-check", "motivation=3"),   # motivation is the user's alone
            ("lamp-list", "motivation=3"),
            ("human", "posting=3"),             # posting belongs to lamp-score
            ("lamp-score", "alumni=Y"),
            ("human", "stage=listed"),          # stage is never written directly
            ("lamp-list", "not_a_column=x"),
        ]
        for writer, pair in cases:
            code, res = self.run_cli("update", "lamp", "--as", writer, "--id", "tidewater-data", "--set", pair)
            self.assertEqual(code, 1, (writer, pair, res))
        self.assertEqual(read(os.path.join(self.d, "lamp.csv")), before)

    def test_values_are_checked(self):
        for pair in ("motivation=5", "hold=yes", "employer="):
            code, _ = self.run_cli("update", "lamp", "--as", "human", "--id", "tidewater-data", "--set", pair)
            self.assertEqual(code, 1, pair)
        code, _ = self.run_cli("update", "lamp", "--as", "lamp-score", "--id", "tidewater-data",
                               "--set", "posting_url=example.com/jobs")
        self.assertEqual(code, 1)

    def test_batch_is_all_or_nothing(self):
        items = json.dumps([
            {"id": "tidewater-data", "motivation": 2},
            {"id": "greenline-grid", "motivation": 9},
        ])
        code, res = self.run_cli("update", "lamp", "--as", "human", "--from", "-", stdin=items)
        self.assertEqual(code, 1)
        self.assertEqual(self.lamp()["tidewater-data"]["motivation"], "3")

    def test_alumni_check_only_updates_contacts_it_found(self):
        self.run_cli("add", "contacts", "--as", "human",
                     "--set", "employer_id=tidewater-data", "--set", "name=Robin Diaz")
        code, _ = self.run_cli("update", "contacts", "--as", "alumni-check", "--id", "c0004",
                               "--set", "affinity=Example Corp")
        self.assertEqual(code, 1)
        code, _ = self.run_cli("update", "contacts", "--as", "alumni-check", "--id", "c0002",
                               "--set", "mutuals=4")
        self.assertEqual(code, 0)

    def test_alumni_check_must_mark_source(self):
        code, _ = self.run_cli("add", "contacts", "--as", "alumni-check",
                               "--set", "employer_id=tidewater-data", "--set", "name=Robin Diaz")
        self.assertEqual(code, 1)
        code, _ = self.run_cli("add", "contacts", "--as", "alumni-check", "--set", "employer_id=tidewater-data",
                               "--set", "name=Robin Diaz", "--set", "source=connections-export")
        self.assertEqual(code, 0)


class TestAdd(Base):
    def test_lamp_list_must_say_where_employers_came_from(self):
        code, _ = self.run_cli("add", "lamp", "--as", "lamp-list", "--set", "employer=Acme Solar")
        self.assertEqual(code, 1)

    def test_duplicates_are_skipped_and_ids_stay_unique(self):
        items = json.dumps([
            {"employer": "Greenline Grid", "bucket": "dream", "source": "suggested"},
            {"employer": "Acme Solar", "bucket": "trending", "source": "suggested"},
            {"employer": "acme  solar", "bucket": "trending", "source": "suggested"},
            {"employer": "Greenline Grid.", "bucket": "hiring", "source": "job-board"},
        ])
        code, res = self.run_cli("add", "lamp", "--as", "lamp-list", "--from", "-", stdin=items)
        self.assertEqual(code, 0, res)
        self.assertEqual([a["id"] for a in res["added"]], ["acme-solar", "greenline-grid-2"])
        self.assertEqual(len(res["skipped"]), 2)
        self.assertEqual(self.lamp()["acme-solar"]["source"], "suggested")

    def test_dry_run_writes_nothing(self):
        before = read(os.path.join(self.d, "lamp.csv"))
        code, res = self.run_cli("add", "lamp", "--as", "human", "--set", "employer=Acme Solar", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(len(res["added"]), 1)
        self.assertEqual(read(os.path.join(self.d, "lamp.csv")), before)


class TestHoldAndEndedConversations(Base):
    def test_hold_blocks_first_message(self):
        code, res = self.run_cli("log", "--as", "outreach-draft", "--event", "drafted",
                                 "--contact", "c0003", "--kind", "initial")
        self.assertEqual(code, 1)
        self.assertIn("on hold", res["errors"][0])

    def test_hold_allows_follow_up_once_started(self):
        self.run_cli("log", "--as", "human", "--event", "sent", "--contact", "c0003", "--kind", "initial")
        code, res = self.run_cli("log", "--as", "follow-up", "--event", "drafted",
                                 "--contact", "c0003", "--kind", "follow_up")
        self.assertEqual(code, 0, res)

    def test_a_draft_alone_does_not_count_as_started(self):
        # c0002 has only a draft (ev0003); put them on hold and try a follow-up.
        self.run_cli("update", "contacts", "--as", "human", "--id", "c0002", "--set", "hold=Y")
        code, _ = self.run_cli("log", "--as", "follow-up", "--event", "drafted",
                               "--contact", "c0002", "--kind", "follow_up")
        self.assertEqual(code, 1)

    def test_declined_ends_the_conversation(self):
        self.run_cli("log", "--as", "human", "--event", "declined", "--contact", "c0001")
        code, _ = self.run_cli("log", "--as", "follow-up", "--event", "drafted",
                               "--contact", "c0001", "--kind", "follow_up")
        self.assertEqual(code, 1)

    def test_only_a_person_confirms_events(self):
        code, _ = self.run_cli("log", "--as", "outreach-draft", "--event", "sent",
                               "--contact", "c0001", "--kind", "initial")
        self.assertEqual(code, 1)
        code, _ = self.run_cli("log", "--as", "human", "--event", "drafted",
                               "--contact", "c0001", "--kind", "follow_up")
        self.assertEqual(code, 1)


class TestLog(Base):
    def test_draft_text_is_saved_under_the_event_id(self):
        code, res = self.run_cli("log", "--as", "outreach-draft", "--event", "drafted", "--contact", "c0002",
                                 "--kind", "follow_up", "--channel", "email", "--draft-from", "-",
                                 stdin="Hi Sam,\n\nFictional draft text.\n")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["event"]["event_id"], "ev0004")
        self.assertEqual(res["event"]["draft"], "drafts/ev0004.md")
        self.assertEqual(res["event"]["employer_id"], "greenline-grid")
        with open(os.path.join(self.d, "drafts", "ev0004.md")) as f:
            self.assertIn("Fictional draft", f.read())

    def test_timestamp_defaults_to_settings_time_zone(self):
        code, res = self.run_cli("log", "--as", "human", "--event", "replied", "--contact", "c0001")
        self.assertEqual(code, 0)
        self.assertIsNotNone(th.parse_timestamp(res["event"]["at"]))

    def test_timestamp_needs_offset(self):
        code, _ = self.run_cli("log", "--as", "human", "--event", "replied", "--contact", "c0001",
                               "--at", "2026-09-27T10:00:00")
        self.assertEqual(code, 1)


class TestFileHandling(Base):
    def test_user_columns_and_column_order_are_preserved(self):
        path = os.path.join(self.d, "lamp.csv")
        data = rows(path)
        header = ["employer", "motivation", "my_priority"] + [c for c in th.LAMP_COLUMNS if c not in ("employer", "motivation")]
        for r in data:
            r["my_priority"] = "high" if r["id"] == "tidewater-data" else ""
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(data)
        code, res = self.run_cli("update", "lamp", "--as", "lamp-score", "--id", "tidewater-data", "--set", "posting=2",
                                 "--set", "posting_url=https://example.com/jobs/789")
        self.assertEqual(code, 0, res)
        with open(path, newline="") as f:
            self.assertEqual(next(csv.reader(f)), header)
        self.assertEqual(self.lamp()["tidewater-data"]["my_priority"], "high")
        code, _ = self.run_cli("update", "lamp", "--as", "lamp-list", "--id", "tidewater-data", "--set", "my_priority=low")
        self.assertEqual(code, 1)
        code, _ = self.run_cli("update", "lamp", "--as", "human", "--id", "tidewater-data", "--set", "my_priority=low")
        self.assertEqual(code, 0)

    def test_crlf_line_endings_are_kept(self):
        path = os.path.join(self.d, "lamp.csv")
        text = read(path, newline="").replace("\n", "\r\n")
        write(path, text, newline="")
        self.run_cli("update", "lamp", "--as", "human", "--id", "tidewater-data", "--set", "notes=CRLF check")
        raw = read(path, "rb")
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_legend_above_header_is_reported(self):
        path = os.path.join(self.d, "lamp.csv")
        text = read(path)
        write(path, ",,,Desirable = 3\n" + text)
        code, res = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertTrue(any("header must be the first row" in e for e in res["errors"]))

    def test_validate_reports_bad_values_and_links(self):
        with open(os.path.join(self.d, "contacts.csv"), "a", newline="") as f:
            f.write("c0009,no-such-employer,Casey,,,,,,,,,\n")
        with open(os.path.join(self.d, "lamp.csv"), "a", newline="") as f:
            f.write("bad-row,Bad Row,,,,5,,,,,,,,\n")
        code, res = self.run_cli("validate")
        self.assertEqual(code, 1)
        joined = "\n".join(res["errors"])
        self.assertIn("no-such-employer", joined)
        self.assertIn("motivation must be one of", joined)

    def test_major_version_mismatch_refuses_to_write(self):
        path = os.path.join(self.d, "job-search.json")
        settings = json.loads(read(path))
        settings["contract_version"] = "1.0"
        write(path, json.dumps(settings))
        code, _ = self.run_cli("update", "lamp", "--as", "human", "--id", "tidewater-data", "--set", "notes=x")
        self.assertEqual(code, 2)

    def test_data_inside_a_git_repo_is_flagged(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        code, res = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertTrue(any("git repository" in w for w in res["warnings"]))


class TestLock(Base):
    def test_busy_lock_times_out(self):
        open(os.path.join(self.d, ".lock"), "w").close()
        with self.assertRaises(th.LockTimeout):
            with th.data_lock(self.d, timeout=0.3):
                pass

    def test_stale_lock_is_cleared(self):
        lock = os.path.join(self.d, ".lock")
        open(lock, "w").close()
        old = time.time() - 120
        os.utime(lock, (old, old))
        with th.data_lock(self.d, timeout=0.3):
            self.assertTrue(os.path.exists(lock))
        self.assertFalse(os.path.exists(lock))

    def test_lock_is_released_after_an_error(self):
        with self.assertRaises(ValueError):
            with th.data_lock(self.d):
                raise ValueError("boom")
        self.assertFalse(os.path.exists(os.path.join(self.d, ".lock")))


class TestRank(Base):
    def test_default_order_and_hold_excluded(self):
        code, res = self.run_cli("rank")
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in res["rows"]], ["greenline-grid", "tidewater-data"])
        code, res = self.run_cli("rank", "--include-hold")
        self.assertEqual([r["id"] for r in res["rows"]][-1], "sunbeam-homes")

    def test_empty_scores_sort_last(self):
        self.run_cli("add", "lamp", "--as", "human", "--set", "employer=Aardvark Energy")
        code, res = self.run_cli("rank")
        self.assertEqual(res["rows"][-1]["id"], "aardvark-energy")


class TestImport(Base):
    def write_sheet(self, header, data):
        path = os.path.join(self.tmp, "sheet.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(data)
        return path

    def test_import_takes_human_columns_and_ignores_the_rest(self):
        data = rows(os.path.join(self.d, "lamp.csv"))
        header = list(data[0].keys()) + ["priority"]
        for r in data:
            r["priority"] = ""
            if r["id"] == "tidewater-data":
                r["motivation"] = "2"
                r["what"] = "Edited in the sheet"
                r["priority"] = "top"
        data.append({**{c: "" for c in header}, "employer": "New Co", "motivation": "1"})
        sheet = self.write_sheet(header, data)
        code, res = self.run_cli("import", sheet)
        self.assertEqual(code, 0, res)
        lamp = self.lamp()
        self.assertEqual(lamp["tidewater-data"]["motivation"], "2")
        self.assertNotEqual(lamp["tidewater-data"]["what"], "Edited in the sheet")
        self.assertEqual(lamp["tidewater-data"]["priority"], "top")
        self.assertEqual(lamp["new-co"]["source"], "user")
        self.assertEqual(lamp["new-co"]["stage"], "listed")
        self.assertEqual(res["ignored_columns"], ["what"])
        self.assertEqual(res["new_columns"], ["priority"])

    def test_import_needs_the_id_column(self):
        data = rows(os.path.join(self.d, "lamp.csv"))
        header = [c for c in data[0] if c != "id"]
        sheet = self.write_sheet(header, [{k: r[k] for k in header} for r in data])
        code, res = self.run_cli("import", sheet)
        self.assertEqual(code, 1)
        self.assertIn("no id column", res["errors"][0])

    def test_rows_missing_from_the_sheet_are_left_alone(self):
        data = [r for r in rows(os.path.join(self.d, "lamp.csv")) if r["id"] == "tidewater-data"]
        sheet = self.write_sheet(list(data[0].keys()), data)
        code, _ = self.run_cli("import", sheet)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.lamp()), 3)


class TestQueueAndTargets(Base):
    def set_settings(self, **changes):
        path = os.path.join(self.d, "job-search.json")
        settings = json.loads(read(path))
        settings.update(changes)
        write(path, json.dumps(settings))

    def test_queue_lists_unchecked_and_stale_rows_in_rank_order(self):
        self.run_cli("add", "lamp", "--as", "human", "--set", "employer=Acme Solar")
        code, res = self.run_cli("queue", "posting")
        self.assertEqual(code, 0, res)
        ids = [r["id"] for r in res["rows"]]
        # Examples were checked 2026-09-21 (stale); sunbeam is on hold; acme was never checked.
        self.assertEqual(ids, ["greenline-grid", "tidewater-data", "acme-solar"])
        self.assertEqual(res["rows"][-1]["reason"], "never checked")

    def test_recent_checks_are_skipped_unless_all(self):
        today = th.today_date({"timezone": "America/Los_Angeles"}).isoformat()
        self.run_cli("update", "lamp", "--as", "lamp-score", "--id", "tidewater-data",
                     "--set", f"posting_checked={today}")
        code, res = self.run_cli("queue", "posting")
        self.assertNotIn("tidewater-data", [r["id"] for r in res["rows"]])
        code, res = self.run_cli("queue", "posting", "--all")
        self.assertIn("tidewater-data", [r["id"] for r in res["rows"]])

    def test_queue_reports_targets_and_warns_when_empty(self):
        code, res = self.run_cli("queue", "posting")
        self.assertTrue(res["targets"]["remote_ok"])
        self.assertEqual(res["warnings"], [])
        self.set_settings(target_roles=[])
        code, res = self.run_cli("queue", "posting")
        self.assertEqual(code, 0)
        self.assertTrue(res["warnings"])
        code, res = self.run_cli("validate")
        self.assertTrue(any("target_roles" in w for w in res["warnings"]))

    def test_bad_target_settings_are_errors(self):
        self.set_settings(remote_ok="yes", posting_max_age_days=0)
        code, res = self.run_cli("validate")
        self.assertEqual(code, 1)
        joined = "\n".join(res["errors"])
        self.assertIn("remote_ok", joined)
        self.assertIn("posting_max_age_days", joined)
        self.assertEqual(self.run_cli("queue", "posting")[0], 2)

    def test_older_settings_still_work(self):
        path = os.path.join(self.d, "job-search.json")
        settings = json.loads(read(path))
        for key in ("target_roles", "target_locations", "remote_ok", "posting_max_age_days"):
            settings.pop(key)
        settings["contract_version"] = "0.2"
        write(path, json.dumps(settings))
        code, res = self.run_cli("validate")
        self.assertEqual(code, 0, res)
        code, res = self.run_cli("queue", "posting")
        self.assertEqual(code, 0, res)

    def test_init_records_targets(self):
        new = os.path.join(self.tmp, "fresh")
        with redirect_stdout(io.StringIO()):
            code = th.main(["init", "--data-dir", new, "--target-role", "Senior Product Designer",
                            "--target-location", "Example City Area", "--remote-ok"])
        self.assertEqual(code, 0)
        settings = json.loads(read(os.path.join(new, "job-search.json")))
        self.assertEqual(settings["target_roles"], ["Senior Product Designer"])
        self.assertTrue(settings["remote_ok"])


class TestOutreachQueue(Base):
    def test_only_scored_employers_with_eligible_contacts(self):
        # Examples: greenline is already contacting, tidewater is scored with no contacts, sunbeam is on hold.
        code, res = self.run_cli("queue", "outreach")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["rows"], [])
        self.assertEqual([n["id"] for n in res["needs_contacts"]], ["tidewater-data"])

    def test_contacts_are_ordered_and_held_contacts_skipped(self):
        items = json.dumps([
            {"employer_id": "tidewater-data", "name": "Far Away", "connection": "3", "source": "user"},
            {"employer_id": "tidewater-data", "name": "Close Friend", "connection": "1", "source": "user"},
            {"employer_id": "tidewater-data", "name": "Many Mutuals", "connection": "2", "mutuals": "40", "source": "user"},
            {"employer_id": "tidewater-data", "name": "Few Mutuals", "connection": "2", "mutuals": "2", "source": "user"},
            {"employer_id": "tidewater-data", "name": "On Hold", "connection": "1", "hold": "Y", "source": "user"},
        ])
        code, res = self.run_cli("add", "contacts", "--as", "human", "--from", "-", stdin=items)
        self.assertEqual(code, 0, res)
        code, res = self.run_cli("queue", "outreach")
        names = [c["name"] for c in res["rows"][0]["contacts"]]
        self.assertEqual(names, ["Close Friend", "Many Mutuals", "Few Mutuals", "Far Away"])

    def test_batch_size_limits_rows(self):
        for n in range(3):
            self.run_cli("add", "lamp", "--as", "human", "--set", f"employer=Batch Co {n}")
            eid = f"batch-co-{n}"
            self.run_cli("update", "lamp", "--as", "human", "--id", eid, "--set", "motivation=2")
            self.run_cli("update", "lamp", "--as", "lamp-score", "--id", eid, "--set", "posting=1")
            self.run_cli("add", "contacts", "--as", "human", "--set", f"employer_id={eid}", "--set", f"name=Person {n}")
        self.set_batch(2)
        code, res = self.run_cli("queue", "outreach")
        self.assertEqual(len(res["rows"]), 2)
        code, res = self.run_cli("queue", "outreach", "--limit", "3")
        self.assertEqual(len(res["rows"]), 3)

    def set_batch(self, size):
        path = os.path.join(self.d, "job-search.json")
        settings = json.loads(read(path))
        settings["outreach_batch_size"] = size
        write(path, json.dumps(settings))


class TestSkillCopies(unittest.TestCase):
    def test_every_skill_carries_the_current_script(self):
        source = read(os.path.join(ROOT, "scripts", "twohours.py"), "rb")
        skills = os.path.join(ROOT, "skills")
        copies = [os.path.join(skills, s, "scripts", "twohours.py") for s in sorted(os.listdir(skills))] \
            if os.path.isdir(skills) else []
        for path in copies:
            self.assertTrue(os.path.exists(path), f"{path} is missing; run scripts/vendor.py")
            self.assertEqual(read(path, "rb"), source, f"{path} is out of date; run scripts/vendor.py")


if __name__ == "__main__":
    unittest.main()
