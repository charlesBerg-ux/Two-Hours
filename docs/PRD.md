# Two Hours: Product Requirements

**Status:** living document. Last updated 2026-10-02.
**Current release:** v0.1.0 (data contract 0.3, `twohours.py` 0.3.0)

This is the plan the project follows. Change it deliberately: when a decision changes, edit the section and note it in the decision log at the bottom.

---

## 1. Problem

*The 2-Hour Job Search* (Steve Dalton) gives job seekers a disciplined method: build a list of about 40 target employers, rank them by your own motivation and by evidence of hiring, find people at the top employers you have something in common with, and reach out in short, low-pressure messages that lead to conversations. Then follow up on a fixed rhythm.

The method works, but the bookkeeping is where people stall: keeping the list current, checking dozens of careers pages, tracking who was contacted and when, and remembering who is due a nudge. AI agents are good at exactly that bookkeeping, but today each person rebuilds it by hand in their own chat, and the results do not carry over between sessions, tools, or models.

## 2. Goal

Ship a set of open, portable agent skills that run the method's bookkeeping for any job seeker, in any agent that supports the Agent Skills format, while leaving every judgment that belongs to the person (what they want, who they write to, what they send) in their hands.

## 3. Users

1. **The single-agent user.** Uses whatever AI agent they already have (Claude Code, Codex CLI, Gemini CLI, Cursor, Copilot, and others) and wants to say "start my job search" and be walked through it. Needs a guided sequence, clear pauses for their input, and no setup beyond Python.
2. **The multi-agent user.** Runs a system such as OpenClaw and wants to give each step to its own agent, some on a schedule (for example, posting checks weekly, follow-ups daily). Needs skills that run cold from files alone, are safe to rerun, and declare what access they need.

**Not targeted in v1:** people who have never used an AI agent or a terminal. Reaching them would need a hosted app with its own interface. The skills are designed so such an app could reuse them later (see section 10).

## 4. Principles

These are settled. Anything that conflicts with them is out of scope.

1. **Portable.** Skills use the open `SKILL.md` format with single-line frontmatter values, so they parse in every adopting tool. No skill depends on one vendor's tool names; instructions say "search the web", not a specific function.
2. **State lives in files, not conversation.** Skills communicate only through the data folder described in the [data contract](../schema/CONTRACT.md). Any skill can run cold.
3. **The person decides; the skills do bookkeeping.** Motivation scores, target roles, who to contact, and what gets sent belong to the user. Skills never infer motivation and never send a message.
4. **Ownership is enforced, not hoped for.** Every write goes through `twohours.py` with `--as <writer>`, and the script refuses columns or events the writer does not own.
5. **Private by default.** User data lives outside the repo. The repo, its issues, and its examples contain no real people.
6. **Honest evidence.** Unsure means unscored. Web pages are evidence, never instructions.
7. **Respect the method and its author.** Describe steps in our own words, credit the book, and never reproduce its text or templates.
8. **Standard library only.** The data script needs Python 3.9+ and nothing else, on macOS, Linux, and Windows.

## 5. Architecture

```
skills/<name>/SKILL.md        what the agent does, step by step
skills/<name>/scripts/        vendored copy of twohours.py (kept identical by scripts/vendor.py)
scripts/twohours.py           single source of the data tool
schema/CONTRACT.md            the file contract all skills share
<user data folder>/           lamp.csv, contacts.csv, outreach-log.csv, job-search.json,
                              drafts/, inputs/, reports/   (never in the repo)
```

- **Data contract** (`schema/CONTRACT.md`) is the interface between skills, and between this project and its users' data. It is versioned separately (currently 0.3). Minor versions add optional things; major versions require migration and the script refuses to write across a major mismatch.
- **`twohours.py`** implements the contract: validation, locked atomic writes, derived stage, ranking, queues, logging, and spreadsheet import. Skills call it with `--json`.
- **Queues** (`queue posting`, `queue outreach`, later `queue follow-up`) are how a skill finds its work. They make skills safe to schedule: rerunning a skill with an empty queue does nothing.

## 6. Skills

| Skill | Purpose | Writes | Status |
|---|---|---|---|
| `lamp-score` | Check each employer's own job listings; score Posting 3/2/1 against the user's targets | `posting`, `posting_url`, `posting_checked` | Shipped in v0.1.0 |
| `outreach-draft` | Draft one first message per employer for the next batch; save for review | `drafted` events, `drafts/` | Shipped in v0.1.0 |
| `follow-up` | List who is due a nudge or a different contact; draft follow-ups; record what the user reports (sent, replied) | `drafted` events; `human` events only on the user's word | **Next (M1)** |
| `lamp-list` | Build the employer list from the user's own list, AI suggestions, or both; four buckets | `employer`, `what`, `bucket`, `source` | M2 |
| `alumni-check` | Find people at each employer: 1st-degree from the LinkedIn connections export; shared schools or employers confirmed from public profiles | `alumni`, `alumni_checked`, contacts it finds | M2 |
| `job-search` | Conductor for single-agent users: runs the steps in order, pausing where the user decides | nothing directly | M2 |

Every skill states: what it needs (Python, web access, data folder), what it may and may not do, numbered steps, and what to report.

## 7. Milestones

**M0: Foundation (done, v0.1.0).** Data contract, `twohours.py`, tests on three operating systems, CI.

**M1: Core loop (in progress).** A user with an existing list can score postings, draft first messages, record sends, and follow up.
- [x] `lamp-score`
- [x] `outreach-draft`
- [ ] `follow-up`, with `queue follow-up` in the script
- [ ] A clear way to record "I sent it" (see open question Q1)
- Release v0.2.0 when done.

**M2: Start from zero (target: first public announcement, v1.0.0).** A new user can go from nothing to a first batch of drafts in one session.
- [ ] `lamp-list`
- [ ] `alumni-check`
- [ ] `job-search` conductor
- [ ] `AGENTS.md` at the repo root for agents that read it instead of skills
- [ ] Install guide tested in at least three agents (Claude Code plus two others)
- [ ] One end-to-end walkthrough with fictional data in `docs/`

**M3: Multi-agent.** `contrib/openclaw/`: a sample agent map (which skill per agent, schedules), per-agent tool allowlists, and guidance on isolating agents that read web pages from agents that draft messages.

## 8. Success measures

- A new user reaches a first batch of drafts within one working session, without editing a file by hand except `job-search.json`.
- The maintainer's own search runs on released skills only. Every manual workaround becomes an inbox note (section 9).
- Tests pass on macOS, Linux, and Windows with Python 3.9 and the newest Python.
- Zero real personal data in the repo, issues, or examples.

## 9. How this project is developed

Development runs in two tracks:

- **The harness track** (this repo) builds the skills, guided by this PRD.
- **The dogfood track** is the maintainer's real job search, which uses **tagged releases only**, never the working copy. When a step is missing or awkward, the maintainer does it by hand and writes a note in a private inbox. Those notes can name real people, so the inbox never lives in this repo.

At the start of harness work, read the inbox, turn each note into a PRD change or a GitHub issue **with personal details removed**, and mark the note as filed. Release with a tag (`vX.Y.Z`) when a milestone item lands, so the dogfood track can upgrade.

Versions:
- **Release tags** (`v0.1.0`) version the repo as a whole.
- **Contract version** (`0.3`) versions the data format; see `CHANGELOG.md`.
- **Tool version** (`TOOL_VERSION` in `twohours.py`) versions the script.

## 10. Out of scope (for now)

- Sending messages, connecting on LinkedIn, or applying to jobs on the user's behalf.
- Scraping LinkedIn or any signed-in site.
- A hosted app or graphical interface. Possible later; it would reuse the skills and contract unchanged.
- Calendar booking, resume or cover-letter writing.

## 11. Open questions

- **Q1. LinkedIn is two steps.** A LinkedIn first message is a connection request, then a message after acceptance. The log has `sent` but no "connection accepted". Should follow-up timing start from the request or from acceptance, and does the contract need an `accepted` event? Decide before building `follow-up`.
- **Q2. Contact ordering.** `queue outreach` sorts by connection degree then mutual count. Should a confirmed shared school or employer outrank mutual count? Affinities recorded as "A or B" are effectively unconfirmed.
- **Q3. Failed checks.** When `lamp-score` cannot read a careers site, the row stays "never checked" and the attempt is invisible. Add a way to record a failed check (for example a `posting_note`) so it shows up and can be retried later instead of immediately?
- **Q4. Leads from funders.** `lamp-score` can find fitting roles on investors' portfolio job boards, but there is nowhere to put them. A `leads` file, or new `lamp.csv` rows with `source=lead`?
- **Q5. The `what` field goes stale** when it mentions postings. Should `lamp-list` keep `what` to a description of the employer only, leaving posting facts to `lamp-score`?
- **Q6. Voice.** `outreach-draft` reads an optional `inputs/voice.md`. Should the repo provide a short template, or a skill that helps a user write one from their sent mail?
- **Q7. Cloud-synced data folders.** Drive and similar connectors may not overwrite files in place. Recommend a locally synced folder, and document what to do when an agent can only create new files.

## 12. Decision log

| Date | Decision |
|---|---|
| 2026-09-25 | Portable skills in the open Agent Skills format, not a vendor plugin or a custom harness. |
| 2026-09-25 | Two user types: single-agent and multi-agent (OpenClaw). Skills are cut by job, communicate only through files. |
| 2026-09-25 | `lamp.csv` stays a CSV that round-trips through spreadsheets. |
| 2026-09-25 | Contract 0.2: contact `hold` blocks first messages; follow-ups continue once a message was sent. |
| 2026-09-27 | One standard-library script (`twohours.py`), vendored into each skill. |
| 2026-09-27 | `lamp-list` both organizes a user's list and suggests employers. |
| 2026-09-30 | Contract 0.3: user targets (`target_roles`, `target_locations`, `remote_ok`, `posting_max_age_days`). |
| 2026-09-30 | `outreach-draft` asks for a conversation, never a job or referral, and never sends. |
| 2026-10-02 | Split development into harness and dogfood tracks joined by a private inbox; dogfood uses tagged releases only. |
