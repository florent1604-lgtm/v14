---
name: collab-discipline
description: >
  Collaboration discipline for Claude, Codex and Hermes on Titanium V14. Use
  before publishing to the common bus or handing off a V14 engineering task.
---

# V14 collaboration discipline

## Five rules

1. **Result first.** Start with the verified number, verdict or blocker.
2. **Self-contained messages.** Include `[ETAT]` and one precise `[DEMANDE]` so
   an agent can resume from cold context.
3. **One local source of truth.** Durable state belongs in `collab/`; the bus is
   the append-only conversation history. Read `collab/HERMES_BRIDGE.md` first.
4. **Correct in place.** Batch known fixes and avoid needless v2/v3/v4 documents.
5. **Measure once, decide once.** Do not repeat costly runs before known defects
   are fixed.

Recommended bus header:

```text
[ETAT] <workstream> | <last verified fact> | <current blocker>
[DEMANDE] <one action> | <deliverable> | <completion criterion>
```

## Roles

- Claude: implementation and heavy environment-dependent runs.
- Codex: independent audit, red-team review and verification.
- Hermes: coordination, memory and non-executable analysis.
- Florent: final human authority.

## Non-negotiable V14 safeguards

- PAPER/DEMO only; no real order.
- No `.env` modification, service restart, execution arming, permission approval
  or destructive action without Florent's explicit authorization for that action.
- No secret in the bus, journal, report or test output.
- No automatic Claude -> Codex -> Claude loop.
- A skill changes method, never authority.

## Five-second check before sending

1. Does the first line contain a result or clear blocker?
2. Can a cold reader understand the context?
3. Is there exactly one requested action and completion criterion?
4. Are durable facts recorded locally?
5. Is the message necessary now?
