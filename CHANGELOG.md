# Changelog

All changes to the data contract (`schema/CONTRACT.md`) are recorded here.

## 0.2 (2026-09-25)

- Added optional `hold` column to `contacts.csv`. Human-only, `Y` or empty. A contact on hold gets no first message and is skipped when choosing a new contact at an employer. Follow-ups continue if outreach to them has already started (a `sent` event exists).
- **Migration:** add a `hold` column to existing `contacts.csv` files. Leaving it empty keeps current behavior.

## 0.1 (2026-09-25)

- Initial draft: `lamp.csv`, `contacts.csv`, `outreach-log.csv`, `job-search.json`, derived stage, lock-based writes, spreadsheet round-trip.
