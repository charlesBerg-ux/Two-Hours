# twohours.py

One script that creates, checks, and safely updates the job-search data described in the [data contract](../schema/CONTRACT.md). Every skill uses it instead of editing the files directly, so the contract's rules hold no matter which agent or AI model is doing the work.

- Standard library only. Python 3.9 or newer, on macOS, Linux, or Windows.
- One file, so each skill can carry its own copy. This file is the source; skill copies are kept identical to it.

## Quick start

```bash
python3 scripts/twohours.py init --data-dir ~/job-search \
  --timezone America/Los_Angeles --affinity "Example University" --affinity "Example Corp"

export TWO_HOURS_DATA=~/job-search      # or pass --data-dir every time

python3 scripts/twohours.py add lamp --as human --set employer="Acme Solar"
python3 scripts/twohours.py update lamp --as human --id acme-solar --set motivation=3
python3 scripts/twohours.py rank
python3 scripts/twohours.py validate
```

Keep the data folder outside this repo and outside any other git repository. `init` and `validate` warn if it is inside one.

## Commands

| Command | What it does |
|---|---|
| `init` | Creates a data folder with empty CSVs, `job-search.json`, and `drafts/`, `inputs/`, `reports/`. Never overwrites existing files. |
| `validate` | Checks every file against the contract. Prints errors and warnings; exits 1 if there are errors. |
| `sync` | Recalculates the `stage` column. The write commands below also do this automatically. |
| `rank` | Prints the ranked employer list using the `sort` setting. Employers on hold are left out unless you pass `--include-hold`. |
| `add lamp` / `add contacts` | Adds rows. IDs are assigned automatically. Employers and contacts already on the list are skipped, not duplicated. |
| `update lamp` / `update contacts` | Changes values in existing rows, by `id`. |
| `log` | Appends an event to `outreach-log.csv`. With `--draft-from`, saves the draft text as `drafts/<event_id>.md`. |
| `import` | Merges a spreadsheet export of `lamp.csv` back in (see contract section 10). |

Every command accepts `--data-dir PATH` and `--json`. With `--json`, the result is printed as a single JSON object with `ok`, `errors`, and `warnings`, plus command-specific fields. Skills should use `--json`.

Write commands accept `--dry-run` to check a change and show what would happen without writing anything.

## Who is writing: `--as`

`add`, `update`, and `log` require `--as`, which names the writer. The script only allows each writer to change the columns the contract gives it.

| `--as` | Can write |
|---|---|
| `human` | `employer`, `motivation`, `hold`, `notes`, and any columns the user added, in `lamp.csv`. Any contact column. Every outreach event except `drafted`. |
| `lamp-list` | `employer`, `what`, `bucket`, `source` in `lamp.csv`. Must set `source` when adding. |
| `alumni-check` | `alumni`, `alumni_checked` in `lamp.csv`. Contacts it found (`source` is `connections-export` or `search`). |
| `lamp-score` | `posting`, `posting_url`, `posting_checked` in `lamp.csv`. |
| `outreach-draft`, `follow-up` | `drafted` events only. |

No writer can set `id` or `stage`.

**Recording something the user said.** When the user tells an agent their motivation rating, or that they sent a message or had a meeting, the agent records it with `--as human`. That is the only honest use of `--as human` by an agent: the user decided it, and the agent is writing it down.

## Rules the script enforces

- **Batches are all or nothing.** If any item in `--from` has an error, nothing is written.
- **Hold blocks new outreach.** A `drafted` event is refused for a contact on hold unless a message has already been sent to them.
- **Ended conversations stay ended.** A `drafted` event is refused for a contact with a `declined` or `closed` event.
- **The log is append-only.** Mistakes are corrected with `log --as human --event void --ref EVENT_ID`.
- **Safe writes.** Every write takes the data folder's `.lock`, rewrites the file through a temporary file and a rename, and keeps unknown columns, column order, and line endings intact. A lock older than 60 seconds is treated as stale.
- **Private by default.** On macOS and Linux, files the script rewrites are readable only by your user account.

## Adding many rows at once

`--from FILE` takes a JSON object or a list of objects. Use `-` to read from standard input.

```bash
echo '[
  {"employer": "Acme Solar", "bucket": "dream", "source": "user"},
  {"employer": "Tidewater Data", "bucket": "trending", "source": "suggested",
   "what": "Ocean monitoring nonprofit"}
]' | python3 scripts/twohours.py add lamp --as lamp-list --from - --json
```

For `update`, each object needs an `id`.

## Logging outreach

```bash
# An agent saves a draft for the user to review
python3 scripts/twohours.py log --as outreach-draft --event drafted \
  --contact c0007 --kind initial --channel email --draft-from draft.md

# The user says they sent it
python3 scripts/twohours.py log --as human --event sent --contact c0007 --kind initial
```

`--at` defaults to now in the `timezone` from `job-search.json`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The data or the requested change breaks the contract. Nothing was written. |
| 2 | Setup problem: no data folder, bad settings, or an unsupported contract version |
| 3 | Another writer held the lock too long. Try again. |

## Tests

From the repo root:

```bash
python3 -m unittest discover -s tests -v
```
