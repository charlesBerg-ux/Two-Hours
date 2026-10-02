# Working on Two Hours

Open agent skills for running *The 2-Hour Job Search* method. This file is for any coding agent working on the repo. Humans: see `README.md`.

## Start here

1. Read `docs/PRD.md`. It is the plan: users, principles, milestones, open questions. The next unchecked item in the current milestone is the default task.
2. **Check the maintainer's inbox** if you have access to it. It holds notes from the maintainer's real job search about what the skills got wrong or could not do. Its location is private: use the path in the `TWO_HOURS_INBOX` environment variable, or ask the maintainer. For each open note, update the PRD or draft a GitHub issue, then mark the note filed in the inbox. **Never copy names, companies, or other personal details from the inbox into the repo or an issue.** Restate the problem with fictional examples.
3. Check what changed recently: `git log --oneline -10`.

## Rules

- **No real personal data in the repo.** Examples, tests, issues, and docs use fictional people and companies only (`Greenline Grid`, `Alex Rivera`, `example.com`).
- **`scripts/twohours.py` is the single source** of the data tool. After changing it, run `python3 scripts/vendor.py` to refresh the copies in `skills/*/scripts/`. A test fails if a copy drifts.
- **Standard library only**, Python 3.9+. No f-string quote reuse (breaks 3.9 to 3.11). Must work on Windows (no time zone database by default).
- **Contract changes are deliberate.** Any change to columns, values, events, or settings updates `schema/CONTRACT.md`, `CHANGELOG.md`, `schema/examples/`, the templates in `schema/`, `CONTRACT_VERSION`, and tests. Minor version for optional additions, major for anything that breaks existing data.
- **Skill files** use single-line frontmatter values (`name`, `description`) so they parse everywhere, and tool-neutral wording ("search the web", not a specific tool name). Each skill states what it needs, what it may and may not do, steps, and what to report. Skills write only through `twohours.py --as <skill-name>`.
- **Skills never send messages, sign in, apply, or contact anyone**, and never infer the user's motivation scores.
- **Credit the book, never reproduce it.** Describe the method in our own words.
- **Writing style for docs:** plain, short sentences; no em-dashes.

## Checks before committing

```bash
python3 scripts/vendor.py
python3 -m unittest discover -s tests
```

CI runs the same tests on Linux, macOS, and Windows with Python 3.9 and 3.13.

## Releasing

When a milestone item lands, tag a release so the maintainer's own search can upgrade:

```bash
git tag -a vX.Y.Z -m "<what changed>"
git push origin vX.Y.Z
```

Update the "Current release" line at the top of `docs/PRD.md` and tick the item in the PRD and the README roadmap.
