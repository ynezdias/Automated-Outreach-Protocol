# Live pilot report — classify service + two-way SMS test (2026-07-30/31)

Written for a reader who was not in the session. Phone numbers are the
owner's own test devices, shown by last four digits.

## What this was

Three things came together and were tested live against real SMS traffic:

1. **The rules-v1 classify service** — deterministic guardrails + keyword
   intent buckets behind an HTTPS endpoint (no ML model yet; that is a later
   milestone). Deployed to AWS, documented in `docs/CLASSIFY_API.md`.
2. **New opener/follow-up copy** — drafted from mining 156k historical
   messages (`research/voice_analysis.py`): the best-performing historical
   openers all end in a direct "how much" question from a named sender.
3. **A live two-way conversation test** — the new opener sent to the owner's
   test phones, every inbound classified by the real production rules, and
   replies sent first hand-approved, then via an unattended local runner.

## Headline results

| # | Result |
| --- | --- |
| 1 | The classify service is live, stable, and correct on everything it claims to handle: STOP suppresses, legal/hostile routes to review, "stop by next week" correctly does NOT suppress, amounts and questions route to a human. |
| 2 | Two revocable bearer tokens exist (owner + manager); which one is used is logged by name on every request. The manager handoff pack is the top of `CLASSIFY_API.md`. |
| 3 | The biggest product gap is now proven with real traffic: **substantive free-text interest lands in `Unclear` and gets dropped or a canned non-answer.** A keyword rules layer cannot carry a conversation — that is the trained classifier's job. |

## The conversation, blow by blow

**Thread A (…7742).** Opener-2 ("…how much would actually help? Reply STOP
to opt out.") → reply *"Yes i an interested tell me more"* → classified
`Interested` (the widened rule caught it **through the typo**) → template
follow-up → *"Around $10000k, how much do you offer?"* → `Question` → routed
to a human by design, and rightly so: a human noticed "$10000k" literally
reads $10M and asked; a template would have steamrolled it → *"10k"* →
`Amount_Given` → qualifying follow-up. **Everything here worked as designed.**

**Thread B (…8925).** This one earned its keep by failing. The unattended
runner answered *"hello ? Maria how much can you offer'"* (no `?` ending, no
keyword → `Unclear`) with a clarifier — then answered *"yes I'm"*, *"yes i
need a loan"*, and finally a textbook-perfect buying signal (*"Looking for
around $75,000 … revenue is hovering around $20,000."*) with **the exact same
clarifier, three times**. None of those match a keyword pattern, all fell to
`Unclear`, and the runner's canned map had no memory. The production system
would never have done this — Unclear is never auto-replied and the loop
breaker forces human takeover after 2 automated replies (the runner logged
that exact warning before repetition #3; the test tool deliberately relaxed
both rules to keep the demo flowing).

**Mechanical failures, both fixed:** the runner died once on a transient DNS
error (no per-cycle error handling — now retries), and the repetition bug is
fixed (Unclear clarifier at most once per thread; never send the same line
twice in a row). The fixed runner (`research/auto_chat.py`, uncommitted) is
**not running** — stopped at the owner's instruction.

## What the pilot validated about the production design

- **Opt-out semantics hold under adversarial phrasing**: "STOP" suppresses
  instantly; "stop by next week" proceeds. Verified against the deployed
  endpoint, not just tests.
- **The Interested-bucket fix earns its place**: "yes im interested" (and
  the live typo variant) now routes to a rep instead of vanishing —
  previously the same silent-drop failure class as Process_Update.
- **Question → human is correct**: the $10M/$10k ambiguity was caught by a
  human in the loop; that judgment is not templatable.
- **The loop breaker and "no auto-reply on Unclear" rules are not
  bureaucracy** — Thread B is what removing them looks like.
- **Em-dash templates cost real money**: the draft Interested template is
  UCS-2 (2 segments) because of one `—`; the ASCII variant is 1 segment. All
  approved templates need a GSM-7 pass before volume sends.

## Open decisions (owner's call, in priority order)

1. **Add `Unclear` to the handoff list.** TAXONOMY.md already says Unclear
   "routes to human", but the deployed default drops it as `no_action`. One
   env-var edit (`CLASSIFY_HANDOFF_INTENTS`), no redeploy. Until the trained
   classifier exists, this is the difference between a rep seeing the
   $75k message and nobody seeing it. Recommended: yes, accepting the
   review-queue volume.
2. **The trained classifier is now the highest-leverage build** — the pilot
   generated concrete failure examples ("yes i need a loan", the $75k
   message) that keyword rules can never cover.
3. **Bearer-vs-SigV4 before Salesforce wiring** (flagged in ADR-023 and
   CLASSIFY_API.md).
4. **Identity-question pattern** ("tell me about yourself" without "?") —
   cheap bucket addition if wanted before the model lands.

## Current state

- **Nothing is sending.** No runner, no scheduler for these test threads.
  The last inbound on …8925 (the $75k message) is unanswered — close it out
  from the TextTorrent UI if desired.
- Classify service live at the Function URL in `CLASSIFY_API.md`;
  `/health` rule hash `bcc263eb…` matches local. Manager handoff = URL +
  manager token (shared out of band) + the doc's start-here block.
- Daily/nightly AWS jobs from earlier work (rewrite canary, suppression
  reconciliation, inbound-gap pager) remain live per ADR-017/018/019.
- Test-thread transcripts: `research/output/` (gitignored, never committed).
