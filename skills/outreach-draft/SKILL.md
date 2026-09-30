---
name: outreach-draft
description: Drafts short first messages to people at the top-ranked employers on a Two Hours job-search list, asking for a brief conversation about their work, and saves them for the user to review and send. Use when the user wants to start outreach, draft networking messages, or work through the next batch of contacts.
---

# outreach-draft

Writes the first message to one person at each of the next employers on the user's ranked list, saves each draft to the data folder, and logs it. The user reviews, edits, and sends every message themselves. This skill never sends anything.

The goal of a first message is a short conversation with someone who works there, about their experience. It is not a job application and it is not a request for a referral.

## What you need

- **Python 3.9 or newer**, to run `scripts/twohours.py` in this skill's folder.
- **The user's data folder**: the path in `TWO_HOURS_DATA`, or ask the user for it. Pass it with `--data-dir PATH` if the variable is not set.
- **Optional: the user's voice.** If `inputs/voice.md` exists in the data folder, read it before drafting and follow it. It is the user's own description of how they write.

In the commands below, `twohours.py` means `scripts/twohours.py` inside this skill's folder.

## What you may and may not do

- You may log only `drafted` events, with `--as outreach-draft`. The script refuses anything else.
- Never send a message, connect with anyone, or mark anything as sent. When the user tells you they sent a draft, log it with `--as human --event sent`, because they did it.
- Never state a fact about the user or the recipient that the data does not support. If a contact's notes say an affinity is unconfirmed, do not mention it in the message.
- Never attach or link a resume, portfolio, or job posting in a first message.

## Steps

### 1. Check the data and get the batch

```bash
python3 twohours.py validate --json
python3 twohours.py queue outreach --json
```

If `validate` reports errors, stop and show them.

`queue outreach` returns the next employers in rank order that have been scored, have no outreach yet, and have at least one contact who can be messaged. Each comes with its contacts, best first: people the user knows directly, then 2nd-degree connections with the most mutual connections, then everyone else. It also lists `needs_contacts`: ranked employers with nobody to message yet.

The batch size comes from `outreach_batch_size` in `job-search.json`. Do not draft more than that unless the user asks.

### 2. Choose one person per employer

Start with the first contact listed, then use judgment:

- Prefer someone whose work is close to the user's target roles, or who leads the team the user would join.
- A shared school or past employer that is confirmed in the data is the strongest opening. Mutual connections are the next best.
- One person per employer per batch. The others are there if this person does not reply.

If you pick someone other than the first contact, say why in your report.

### 3. Write each draft

Each message:

- **Opens with the real connection**, in the first sentence: the shared school or employer, or how the user knows them. Only facts from the data.
- **Says who the user is in one sentence**, chosen for relevance to this person and this employer. One relevant fact beats a summary of a career.
- **Asks for a short conversation** (15 to 20 minutes) about the person's experience at the employer or the work their team does. Not a job, not a referral, not a review of the user's resume.
- **Is easy to say yes to**, and just as easy to ignore without awkwardness.
- **Is short.** An email should fit on a phone screen without scrolling, about 100 words or fewer. Write the way the user talks, not the way a cover letter reads.

Pick the channel:

- **Email** if the contact has an `email`. Include a plain, specific subject line.
- **LinkedIn** otherwise. Write two parts: a **connection note** of one or two sentences (LinkedIn limits its length), and a **message** to send after they accept.

Save each draft as a Markdown file in a scratch location with this shape:

```markdown
# Draft: <Contact name>, <Employer>

- Channel: linkedin | email
- Contact: <contact id>
- Why this person: <one line>
- Check before sending: <anything the user should confirm, or "nothing">

## Connection note        (LinkedIn only)

<text>

## Subject                (email only)

<text>

## Message

<text>
```

### 4. Log each draft

```bash
python3 twohours.py log --as outreach-draft --event drafted \
  --contact <contact id> --kind initial --channel linkedin --draft-from <file> --json
```

The script saves the draft as `drafts/<event id>.md` in the data folder, records the event, and moves the employer to the `contacting` stage. It refuses a first message to a contact on hold, and any draft to someone whose conversation was declined or closed.

### 5. Report to the user

Give a short report:

- Each draft: who, at which employer, which channel, and where the file is.
- Anything to check before sending (for example, a fact you could not confirm).
- Employers skipped because they need a contact first, so the user can find someone or run an alumni check.
- A reminder that nothing has been sent, and that once they send a message they can say so and you will log it.

## Quick rules

- One person per employer per batch.
- Connection first, then who the user is, then a small, specific ask.
- Only facts the data supports.
- Drafts only. The user sends.
