# Two Hours

Open agent skills for running a job search with the method from Steve Dalton's *The 2-Hour Job Search*: build a list of target employers, find people you have something in common with, and reach out in a way that turns into conversations.

The skills use the open [Agent Skills](https://agentskills.io) format (`SKILL.md` folders), so they are meant to work in any agent that supports it, whichever AI model that agent runs. That includes Claude Code, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and OpenClaw.

> **Status: early.** The shared data contract is drafted. The skills and scripts are not written yet. See the roadmap below.

## Two ways to use it

1. **One agent, start to finish.** Ask your agent to start a job search. A conductor skill walks through each step in order and pauses for your input where the method needs your judgment.
2. **Split across agents.** Each step is its own skill with defined inputs and outputs, so a multi-agent setup such as OpenClaw can give each step to a separate agent, and run follow-ups on a schedule.

Both work because the skills never pass state through conversation. They read and write a small set of files described in the [data contract](schema/CONTRACT.md).

## How the steps map to skills

| Skill | What it does |
|---|---|
| `lamp-list` | Builds the target employer list. It can organize employers you supply, suggest employers for you to consider, or both. |
| `alumni-check` | Finds people at each employer who share your schools or past employers, using your LinkedIn connections export. |
| `lamp-score` | Checks each employer for relevant job postings. You rate your own motivation; the skill never guesses it. |
| `outreach-draft` | Drafts short outreach messages for your approval. It never sends anything. |
| `follow-up` | Tells you who is due a follow-up and drafts it, or suggests a different contact when someone goes quiet. |
| `job-search` | Runs the steps above in order, for people using a single agent. |

## Your data stays yours

Your employer list, contacts, and outreach log live in a data folder you choose, outside this repo. `.gitignore` excludes common data folder names, but keep your data folder somewhere else entirely so it can never be committed. Please never paste real contact details into issues.

No skill scrapes LinkedIn or sends messages on your behalf. Alumni checks use the connections file LinkedIn lets you export, and every message goes out only when you send it.

## Roadmap

- [x] Data contract (`schema/`)
- [ ] Shared scripts: stage sync, safe file writes, validation
- [ ] `lamp-list` skill
- [ ] `alumni-check` skill
- [ ] `lamp-score` skill
- [ ] `outreach-draft` skill
- [ ] `follow-up` skill
- [ ] `job-search` conductor skill
- [ ] `AGENTS.md` for agents that read it
- [ ] `contrib/openclaw/`: sample agent map and tool allowlists

## About the method

This project implements the workflow described in *The 2-Hour Job Search* by Steve Dalton. It describes the steps in its own words and does not reproduce the book's text or templates. Read the book for the method and the reasoning behind it.

This project is not affiliated with or endorsed by Steve Dalton or the book's publisher.

## License

[MIT](LICENSE)
