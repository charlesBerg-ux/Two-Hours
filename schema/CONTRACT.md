# Data contract

**Contract version:** 0.3 (draft)

This file defines the shared data that every skill in this repo reads and writes. Skills never pass state to each other through conversation. They pass it through the files described here. That is what lets one person run the whole search in a single agent session, and lets an OpenClaw user split the same work across several agents without them colliding.

The workflow follows the method in Steve Dalton's *The 2-Hour Job Search*. This contract describes file formats only. Read the book for the method itself. This project is not affiliated with the book or its author.

## 1. Files

All user data lives in one **data directory** that the user chooses. It must sit outside this repo and outside any single agent's workspace, so every agent can reach it and nothing is ever committed.

```
<data_dir>/
  job-search.json      settings and contract version
  lamp.csv             the employer list (one row per employer)
  contacts.csv         people at those employers (one row per person)
  outreach-log.csv     append-only record of every outreach event
  drafts/              message drafts, one markdown file per draft
  inputs/              files the user supplies, read-only to agents
  reports/             generated summaries, safe to delete
  .lock                present only while a write is in progress
```

The user's LinkedIn connections export, if they provide one, goes in `inputs/`. Agents read it and never modify it.

## 2. CSV conventions

These apply to all three CSV files.

- UTF-8, comma-delimited, standard CSV quoting (RFC 4180). Any value containing a comma, quote, or line break is quoted.
- Exactly one header row, and it is the first row. Column names are the snake_case names in this document.
- **Address columns by name, never by position.** Users may reorder columns in a spreadsheet.
- **Preserve unknown columns.** A user may add their own columns. Every writer keeps them intact and in place.
- An empty value means "unknown" or "not yet checked." It never means no or zero.
- Dates use `YYYY-MM-DD`. Timestamps use ISO 8601 with a UTC offset, for example `2026-09-25T14:05:00-07:00`.
- Yes/no fields use `Y` or `N`.
- Multi-value fields separate items with `; ` (semicolon and space).
- Row order carries no meaning. The ranked view is computed (section 7), not stored.

## 3. lamp.csv

One row per employer. Columns appear in this order in the template so the file reads like the method's worksheet, but scripts must still use names.

| Column | Type | Allowed values | Written by | Meaning |
|---|---|---|---|---|
| `id` | text | lowercase `a-z`, `0-9`, `-`; unique | `lamp-list` | Permanent key. Created once from the employer name and never changed, even if the name is corrected. |
| `employer` | text | | `lamp-list`, human | Employer name as the user wants it shown. |
| `what` | text | up to about 140 characters | `lamp-list` | One line on what the employer does and why it made the list. |
| `bucket` | enum | `dream`, `alumni`, `hiring`, `trending` | `lamp-list` | Which of the four list-building sources produced this employer. |
| `alumni` | enum | `Y`, `N`, empty | `alumni-check` | `Y` if at least one contact in `contacts.csv` shares an affinity with the user. Empty means not checked yet. |
| `motivation` | integer | `3`, `2`, `1`, `0`, empty | human | The user's own gut rating: 3 desirable, 2 middle tier, 1 lower tier, 0 don't know. |
| `posting` | integer | `3`, `2`, `1`, empty | `lamp-score` | 3 a posted role fits the user, 2 the employer is hiring but not for a fitting role, 1 no relevant postings found. |
| `stage` | enum | see section 6 | `twohours.py` | Derived status. Never edited by hand or by an agent's judgment. |
| `hold` | enum | `Y`, empty | human | `Y` parks the employer without deleting it. |
| `notes` | text | | human | Anything the user wants to remember. |
| `source` | text | `user`, `suggested`, `job-board`, `connections-export`, or other | `lamp-list` | Where the employer came from. `suggested` means an AI proposed it. |
| `alumni_checked` | date | | `alumni-check` | When the alumni check last ran for this row. |
| `posting_url` | URL | | `lamp-score` | Evidence for a `posting` value of 2 or 3. |
| `posting_checked` | date | | `lamp-score` | When postings were last checked. |

**Motivation belongs to the user.** A skill may ask for it and record the answer (as `human`, since the user decided it), but it must never infer, suggest, or fill in a motivation score. The whole point of the rating is that it is the user's.

**Contact names do not go in lamp.csv.** People live in `contacts.csv`, linked by `employer_id`, so there is exactly one place to update or delete them.

## 4. contacts.csv

One row per person.

| Column | Type | Allowed values | Written by | Meaning |
|---|---|---|---|---|
| `id` | text | `c` followed by digits, for example `c0007`; unique | creating writer | Permanent key. |
| `employer_id` | text | an existing `lamp.csv` id | creating writer | The employer this person works at now. |
| `name` | text | | creating writer, human | |
| `title` | text | | creating writer, human | Current role. |
| `affinity` | text list | | `alumni-check`, human | Shared backgrounds, for example `Google; University of Michigan`. |
| `connection` | integer | `1`, `2`, `3`, `0`, empty | `alumni-check`, human | Network distance. `0` means no connection, for example an alum found through a school directory. |
| `mutuals` | integer | | `alumni-check`, human | Shared connections, if known. |
| `source` | text | `connections-export`, `search`, `referral`, `user` | creating writer | How this person was found. |
| `profile_url` | URL | | creating writer, human | |
| `email` | text | | human | Optional. Only store it if the user will actually email this person. |
| `hold` | enum | `Y`, empty | human | `Y` means do not contact this person for now. The row and its history stay intact. |
| `notes` | text | | human | |

**Row ownership:** `alumni-check` may create rows and update the columns it writes, but only on rows whose `source` it set. Humans may edit any row. `outreach-draft` and `follow-up` only read this file.

**Contact hold:** `hold` = `Y` blocks new outreach to that person, but it does not interrupt a conversation that has already started. Outreach to a contact has started once the log has a `sent` event for them that is not voided. A `drafted` event alone does not count.

- `outreach-draft` never drafts a first message to a contact on hold.
- When `follow-up` needs a new person at an employer, it skips contacts on hold and picks someone else.
- `follow-up` still drafts follow-ups to a contact on hold if outreach to them has already started.

Only the user sets or clears `hold`. Use it for someone you would rather reach later or through a mutual connection. Someone who said no is recorded as a `declined` event in the outreach log instead.

## 5. outreach-log.csv

An append-only record of events. Nothing in this file is edited or deleted. A mistake is corrected by appending a `void` event that points at the wrong one.

| Column | Type | Allowed values | Meaning |
|---|---|---|---|
| `event_id` | text | `ev` followed by digits; unique, increasing | Assigned by the write script. |
| `at` | timestamp | | When the event happened, not when it was recorded. |
| `contact_id` | text | an existing `contacts.csv` id | |
| `employer_id` | text | an existing `lamp.csv` id | Repeated here so employer views don't need a join. |
| `event` | enum | `drafted`, `approved`, `sent`, `replied`, `meeting_scheduled`, `meeting_held`, `declined`, `closed`, `void` | |
| `kind` | enum | `initial`, `follow_up`, `thank_you`, `other` | What the message was for. |
| `channel` | enum | `email`, `linkedin`, `other` | |
| `draft` | path | relative to `<data_dir>`, for example `drafts/ev0012.md` | Set on `drafted` events. |
| `actor` | text | a skill name, or `human` | Who produced the event. |
| `ref` | text | an earlier `event_id` | Required on `void`, otherwise empty. |
| `note` | text | | |

**Who may append which events:**

- `drafted` is appended by `outreach-draft` or `follow-up`.
- Every other event records something only the user can confirm: that a message was approved, sent, or answered, or that a meeting happened. An agent may append these only when the user has told it so in the current session, and it records `actor` as `human`.
- **No skill sends messages.** Sending is always done by the user, outside this system.

## 6. Stage (derived)

`stage` in `lamp.csv` is recalculated by `twohours.py` (the `sync` command, and automatically after every write) from the other columns and the outreach log. The first matching rule wins.

| Stage | Rule |
|---|---|
| `hold` | `hold` is `Y` |
| `meeting` | any `meeting_scheduled` or `meeting_held` event for this employer that is not voided |
| `engaged` | any `replied` event for this employer that is not voided |
| `contacting` | any `drafted` or `sent` event for this employer that is not voided |
| `scored` | `motivation` and `posting` are both filled |
| `listed` | otherwise |

Because stage is derived, agents use it as a work queue without ever writing it. For example, the outreach agent works on `scored` rows and the follow-up agent works on `contacting` rows.

## 7. Ranked view

The ranked list is computed on demand and never stored. The default sort, configurable in `job-search.json`:

1. `motivation`, highest first
2. `posting`, highest first
3. `alumni`, `Y` before `N` before empty
4. `employer`, alphabetical, as a tiebreaker

Rows with `hold` = `Y` are excluded.

## 8. Settings: job-search.json

```json
{
  "contract_version": "0.3",
  "timezone": "America/Los_Angeles",
  "list_target": 40,
  "buckets": ["dream", "alumni", "hiring", "trending"],
  "affinities": ["Example Corp", "Example University"],
  "sort": ["motivation desc", "posting desc", "alumni desc", "employer asc"],
  "target_roles": ["Senior Product Designer", "Staff Product Designer", "Design Lead"],
  "target_locations": ["Example City Area"],
  "remote_ok": true,
  "posting_max_age_days": 7,
  "outreach_batch_size": 5,
  "follow_up": {
    "try_another_contact_after_business_days": 3,
    "nudge_same_contact_after_business_days": 7
  }
}
```

`affinities` lists the user's schools and past employers. `alumni-check` matches against it. The follow-up timings are defaults and can be changed.

`target_roles`, `target_locations`, and `remote_ok` say what a fitting job looks like. `lamp-score` uses them to decide between a `posting` of 3 (a posted role fits) and 2 (hiring, but nothing fits). Roles are written the way the user would describe them; the skill reads them with judgment, not as exact strings. `posting_max_age_days` is how old a posting check can be before `lamp-score` checks that employer again. Postings change fast, so the default is 7.

The user owns these settings. An agent may write them only with values the user has given.

## 9. Writing safely

Most violations of this contract happen when two writers rewrite the same file at once, even if they own different columns. These rules prevent lost updates.

1. **Write through [`scripts/twohours.py`](../scripts/README.md)** when the agent can run code. It implements the rules below and the ownership rules in sections 3 to 5.
2. **Lock before writing.** Create `<data_dir>/.lock` exclusively. If it already exists, wait and retry. A lock older than 60 seconds is stale and may be removed.
3. **Read, modify, write, in one locked step.** Read the whole file, change only the columns and rows this writer owns, write to a temporary file in the same directory, then rename it over the original.
4. **Keep everything else intact:** other writers' columns, unknown columns, and row order.
5. **Release the lock** by deleting `.lock`.

**Single-agent mode.** When only one agent can write, as in a single chat session with no code execution, the agent may edit files directly. It must still follow the ownership rules in sections 3 to 5.

## 10. Spreadsheet round-trip

`lamp.csv` is designed to round-trip through Google Sheets or Excel.

- **Export:** import `lamp.csv` into a sheet with its header as row 1. The `id` column may be hidden but must not be deleted.
- **Google Drive note:** opening `lamp.csv` in Google Sheets from Drive creates a separate Sheets file and leaves the CSV unchanged. That is fine: edit the sheet, then merge it back with `import` (download it as CSV first). Before the next round of edits, refresh the sheet from the current `lamp.csv` so it shows the latest values from the skills.
- **Import back:** `twohours.py import` merges the sheet into `lamp.csv` by `id`. It accepts changes only to human-owned columns (`employer`, `motivation`, `hold`, `notes`) plus any user-added columns. Changes to agent-owned columns are ignored, because the file stays the source of truth for them.
- A sheet row with no `id` is treated as a new employer added by the user (`source` = `user`).
- Legends or notes above the header row break the round-trip. Put them in a cell note or a separate tab.

## 11. Privacy

`contacts.csv`, `outreach-log.csv`, and `drafts/` hold personal information about real people who did not sign up for this tool.

- The data directory must never be committed. The repo's `.gitignore` excludes common data directory names, but the safest setup keeps the data directory outside the repo entirely.
- Store only what the search needs. Do not import every field from a connections export.
- Never paste real rows into public issues or examples. Use fictional data like `examples/`.
- If someone asks to be removed, delete their `contacts.csv` row and their drafts, and void their log events.

## 12. Versioning

- **Minor version** (0.1 to 0.2): adds an optional column or a new allowed value. Old files still validate.
- **Major version** (0.x to 1.0, or 1.x to 2.0): renames, removes, or changes the meaning of anything. Scripts compare the major version in `job-search.json` with their own and refuse to write on a mismatch.
- Every change is recorded in `CHANGELOG.md` with a migration note.
