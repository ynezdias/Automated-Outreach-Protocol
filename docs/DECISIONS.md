# Architecture Decision Records

Decisions are recorded as lightweight ADRs. Copy the template below for each new
decision and number it sequentially.

## ADR Template

```markdown
## ADR-NNN: Title

- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Superseded by ADR-NNN

### Context

What situation or problem prompted this decision?

### Decision

What was decided, stated in the active voice.

### Consequences

What becomes easier or harder as a result, including trade-offs accepted.
```

---

## ADR-001: Python 3.12 managed with uv

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The project needs a single, reproducible Python toolchain for infrastructure code,
pipelines, and tests.

### Decision

Use Python 3.12 with [uv](https://docs.astral.sh/uv/) for dependency management,
virtual environments, and lockfile (`uv.lock`).

### Consequences

Fast, deterministic installs locally and in CI via `uv sync`. Contributors must have
uv installed; pip-based workflows are not supported.

## ADR-002: Ruff for linting and formatting

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

A single fast tool is preferred over separate linter/formatter/import-sorter stacks.

### Decision

Use Ruff for both linting (`ruff check`) and formatting (`ruff format`), configured in
`pyproject.toml`, enforced in `make check`, pre-commit, and CI.

### Consequences

One config, one tool, sub-second runs. Rule selection is curated in
`[tool.ruff.lint]` and can be tightened over time.

## ADR-003: mypy in strict mode

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

Infrastructure and pipeline code benefits from catching type errors before deploy;
retrofitting strictness later is expensive.

### Decision

Run `mypy --strict` (via `strict = true` in `pyproject.toml`) across `infra/` and
`tests/` from day one.

### Consequences

All code is fully annotated from the start. Some third-party stubs may need
per-module overrides as dependencies are added.

## ADR-004: pytest for testing

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The project needs a standard unit-test runner integrated with `make check` and CI.

### Decision

Use pytest with tests under `tests/`, configured in `pyproject.toml`.

### Consequences

Familiar fixtures/parametrize idioms; CDK assertions can use
`aws_cdk.assertions` inside pytest tests as stacks gain resources.

## ADR-005: AWS CDK in Python with four stacks

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

Infrastructure should be defined as code in the same language as the rest of the
project, split along deployment and ownership boundaries.

### Decision

Use AWS CDK (Python, `aws-cdk-lib` v2) with four stacks: **data**, **pipeline**,
**inference**, and **observability**, defined as `infra/<name>_stack.py` modules and
wired in `infra/app.py`.

### Consequences

Stacks can be deployed and evolved independently. Cross-stack references must be
managed deliberately as resources are added.

## ADR-006: Salesforce development via SFDX with scratch orgs

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

Salesforce metadata must be versioned in git and validated against disposable
environments rather than shared sandboxes.

### Decision

Keep an SFDX project under `salesforce/` with source-format metadata in
`force-app/` and a scratch org definition in `config/project-scratch-def.json`.
CI validates deploys against a fresh scratch org.

### Consequences

Salesforce changes are reviewable and repeatable. A Dev Hub org and the
`SFDX_AUTH_URL` CI secret are required for scratch org creation.

## ADR-007: GitHub Actions CI with pre-commit guardrails

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

Every PR should prove the project lints, typechecks, tests, synthesizes, and
validates against Salesforce before merge, with cheap local feedback first.

### Decision

Use GitHub Actions (`.github/workflows/ci.yml`) running lint, typecheck, unit
tests, `cdk synth`, and `sf project deploy` validation. Locally, pre-commit runs
Ruff and detect-secrets (with a committed `.secrets.baseline`) on every commit.

### Consequences

Broken code and leaked secrets are caught before merge. Contributors run
`uv run pre-commit install` once per clone; the Salesforce job self-skips when the
Dev Hub secret is absent so forks and early PRs still pass.

## ADR-008: Dedicated Object Lock bucket for the audit zone

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The audit zone requires S3 Object Lock in compliance mode with a 7-year default
retention. Object Lock is a bucket-level property that must be set at bucket
creation and cannot be scoped to a prefix; enabling it on the shared data bucket
would make every zone (raw/, staging/, etc.) immutable for 7 years.

### Decision

Split storage into two buckets in the data stack: a data bucket holding the
raw/, staging/, suppression/, conversations/, training/, and models/ prefixes,
and a dedicated audit bucket with Object Lock in compliance mode
(7-year default retention), its own customer-managed KMS key, and no lifecycle
expiration. Audit objects are still written under an audit/ key prefix so the
logical zone layout is uniform.

### Consequences

Audit records are immutable and cannot be shortened or deleted even by the root
account until retention lapses — 7 years is a floor, so miswritten objects also
persist. Consumers must address the audit bucket by its own name/ARN rather than
a prefix on the data bucket.

## ADR-009: Suppression list design — append-only S3 event log, fail-closed checks

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The suppression list is a compliance control: an opt-out recorded anywhere must
be queryable within 60 seconds, suppression is cross-channel, and normalization
must be identical to the cleansing pipeline. S3 objects cannot be appended to,
and several conventional tool choices (pyarrow, moto→cryptography) ship no
Windows-ARM64 wheels, which our dev machines require.

### Decision

- **Source of truth** is an append-only event log: one immutable JSON object per
  suppression event under `suppression/events/<uuid>.json`. Unique keys make
  concurrent writes lossless. The Parquet snapshot
  (`suppression/optout/current.parquet`) and the in-memory query cache are
  derived and always rebuildable from the log.
- **Freshness**: reads go through a cache with a 30-second TTL (constant
  `REFRESH_TTL_SECONDS`, test-asserted `< 60`). Writers see their own writes
  immediately.
- **Fail-closed**: a provided identifier that cannot be normalized is reported
  as suppressed (`indeterminate_identifiers`); a query with no identifiers
  raises. `add_suppression` raises on unnormalizable input so the opt-out routes
  to a human instead of being silently dropped.
- **Shared normalization** lives in `src/cleansing/normalize.py` (E.164 via
  phonenumbers; emails NFKC-folded, casefolded, zero-width-stripped, and
  plus-address tags removed — over-suppression is safe, under-suppression is a
  violation).
- **Earliest opt-out wins** when an identifier has multiple events.
- **Tooling substitutions**: polars writes/reads Parquet (pyarrow has no
  win-arm64 wheel); tests inject a minimal in-memory fake S3 client instead of
  moto (moto requires cryptography, which has no win-arm64 wheel and needs a
  Rust toolchain to build). Revisit moto if the platform constraint lifts.

### Consequences

No lost opt-outs under concurrency and full auditability of every suppression.
Reads can be up to 30s stale — within the 60s bound but not instantaneous.
Plus-addressed variants of an opted-out mailbox are all suppressed. The fake S3
client must be kept faithful to the real API surface it mimics (put/get/list).

## ADR-010: Cleansing pipeline shape — S3-chained step Lambdas, quarantine per step

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The cleansing pipeline (schema validate → normalize → validate → dedupe →
suppress → assign ID → write staging) must let any step be rerun in isolation,
and several details were not fixed by the work order: where intermediates live,
how quarantine works after step 1, what Twilio line type is used for, and where
"last contacted" comes from for the cooldown rule.

### Decision

- Steps exchange newline-delimited JSON under `staging/pipeline/<run_id>/`
  (inspectable intermediates); each handler's return value is the next
  handler's event, so Step Functions chains them with `payload_response_only`
  and a human can rerun any step by hand-crafting the same shape.
- Every filtering step (1-3) writes its own quarantine CSV under
  `raw/quarantine/<run_id>/<step>.csv` with a `reason` column; step 5 writes
  excluded rows (suppressed/cooldown) to an audit ndjson rather than dropping
  them silently.
- Twilio Lookup is gated by `TWILIO_LOOKUP_ENABLED` (default **false** — it
  costs $0.01/query) and only ever annotates `line_type`; dropping landlines is
  a downstream human decision. An email domain with no MX is nulled, and the
  row is quarantined only if that leaves no identifier (fail toward review, not
  toward sending).
- Cooldown uses the `last_contacted_at` column supplied in the enrichment feed
  (Salesforce is the source of that value), compared against `as_of` (event
  override for reproducible reruns, else now) with `COOLDOWN_DAYS` env, default 90.
- The Salesforce external ID is UUIDv5 over `"<e164-phone>|<email>"` with a
  fixed project namespace — stable across runs; the namespace must never change.
- The trigger is EventBridge (bucket notifications enabled) on Object Created
  under `raw/enrichment/`, targeting the state machine.
- Lambda code ships as a source asset; bundling third-party deps (polars,
  phonenumbers, dnspython) is a deploy-time follow-up before first real deploy.
- moto is installed via an environment marker (`platform_machine != 'ARM64'`):
  the integration test uses moto in CI and the in-memory fake locally (ADR-009).

### Consequences

Any step can be rerun from its predecessor's output object. Quarantine and
exclusion are fully auditable per run. The pipeline never deploys with paid
lookups silently enabled. Until dependency bundling lands, deploying the stack
produces Lambdas that cannot import their dependencies — synth and tests are
the current acceptance surface.

## ADR-011: Salesforce data model — provisional intent taxonomy, template immutability

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The Salesforce metadata work order references plan section 6, which is not in
the repo; the 12-intent taxonomy is not enumerated anywhere versioned. Several
modeling details also needed fixing: where the taxonomy lives, sharing models,
and how template immutability is enforced.

### Decision

- **Provisional 12-intent taxonomy** as the `Outreach_Intent` Global Value Set
  (single source referenced by Lead, Outreach_Message__c, Reply_Template__c):
  Interested, Question, Request_More_Info, Not_Interested, Not_Now,
  Already_Financed, Wrong_Person, Referral, Opt_Out, Legal_Escalation,
  Auto_Reply, Unclear. Opt_Out and Legal_Escalation are guardrail-detected
  (regex/keywords, never ML) but remain in the taxonomy so records carry one
  consistent label. **Rename/replace values before real data exists** if the
  plan's taxonomy differs — API names are load-bearing after that.
- **Idempotency/upsert keys**: `Outreach_Message__c.Provider_Message_Id__c`
  (case-sensitive) and `Lead.Outreach_External_Id__c` (case-insensitive, holds
  the pipeline's UUIDv5) are both External ID + Unique.
- **Template immutability** via two validation rules: Approved → Draft blocked;
  `Body__c` edits blocked whenever PRIORVALUE(Status) is Approved — including
  edits smuggled into the same update that retires the record. Approved →
  Retired is the only exit. Enforced with Apex tests (ReplyTemplateLockTest).
- **Least privilege**: Outreach_Integration_User grants R/C/E (never delete) on
  Lead and Outreach_Message__c, read-only Reply_Template__c, FLS on exactly the
  outreach fields plus Lead.Email/Phone, and nothing else — no Modify All Data,
  no View All. OutreachIntegrationUserAccessTest pins the boundary (built on
  the Minimum Access - Salesforce profile). Outreach_Reviewer is a separate set
  for the human queue: work leads, read threads, record manual messages,
  read-only templates.
- **Sharing model ReadWrite** (org-internal) for both custom objects in v1;
  tightening to Private requires sharing rules for the reviewer queue and is
  deferred.
- Lookups from Outreach_Message__c use **Restrict delete** so leads/templates
  with sent messages cannot be deleted (reconstructability).

### Consequences

The webhook double-logging invariant is enforced at the database layer, not
just in code. Template edits after approval force new version records. The
integration user's blast radius is limited to the outreach objects. If the
plan's section 6 taxonomy differs, the value set must be reconciled before any
classifier training data is labeled against it.

## ADR-012: Salesforce sync — JWT via pure-python rsa, fake-first integration tests

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The sync layer needs RS256 signing for the JWT Bearer Flow, and the acceptance
test calls for a scratch org. `cryptography` (the usual RS256 provider) has no
Windows-ARM64 wheel, and a real scratch-org run requires a Connected App with a
certificate that does not exist yet.

### Decision

- **JWT signing uses the pure-python `rsa` package.** The Secrets Manager
  secret (`outreach/salesforce/jwt`) stores JSON with `client_id`, `username`,
  `login_url`, and `private_key` — the key in **PKCS#1 PEM** ("BEGIN RSA
  PRIVATE KEY", `openssl rsa -traditional`), which `rsa` can load. Tokens are
  minted per invocation; no refresh tokens, no password flow anywhere.
- **HTTP goes through an injectable transport** (stdlib urllib by default, no
  requests dependency). 429/503 retry with exponential backoff (1/2/4/8s, five
  attempts); every response's `Sforce-Limit-Info` header is captured and
  surfaced to CloudWatch as `ApiUsagePercent`.
- **One Bulk API 2.0 upsert job per 10,000 records** on
  `Outreach_External_Id__c`. A job ending in any state but JobComplete raises
  (fail loud); per-record failures are fetched from `failedResults` and written
  verbatim to `staging/sync-failures/<run_id>/<job_id>.csv`.
- **Acceptance tests run against an in-memory FakeSalesforce** that verifies
  the JWT signature with the real public key and implements true upsert
  semantics; the identical flow runs against a real scratch org when
  `SALESFORCE_SYNC_TEST_SECRET` is set (requires a Connected App + certificate
  and the JWT pre-authorized for the integration user — manual setup, then CI).
- **Reconciliation** compares distinct external IDs in `staging/cleansed/`
  (runs overlap; upsert dedupes) against Salesforce leads with an external ID,
  publishing `ReconciliationDriftPercent` daily; the observability stack alarms
  above 1%, and missing data breaches (a silent reconciler is an incident).
- Sync runs as the final Step Functions state after write_staging; reconcile is
  EventBridge-scheduled daily.

### Consequences

Everything runs and is fully tested on any platform without native crypto; the
scratch-org path exercises byte-identical code once credentials exist. The
PKCS#1 key format is a hard requirement operators must follow when creating the
secret. Reconciliation counts leads, not field-level drift — that is a later
concern.

## ADR-013: Send path — check ordering, throttle config, callback idempotency

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The send path's order of operations is fixed (suppression -> quiet hours ->
rate limit -> send -> log), but several platform constraints and unspecified
details needed decisions: Apex forbids callouts after DML, scheduled Flows
cannot make callouts in their own transaction, Twilio needs the Account SID in
the URL, and quiet-hours timezone handling has a dangerous platform default.

### Decision

- **Ordering**: enforced per lead inside `OutreachSendService.sendBatch`.
  Because callouts must precede DML, all sends happen first and all logging DML
  commits at the end of the transaction; the decision order per lead is exactly
  the mandated one, verified by tests (suppressed+unknown-tz -> `suppressed`;
  unknown-tz+zero-limit -> `unknown_timezone`).
- **Secrets**: the Twilio auth token lives ONLY in the `Twilio` Named
  Credential (Password protocol; admin enters credentials post-deploy). The
  Account SID and From number — identifiers, not secrets — live in
  `Outreach_Setting__mdt`, alongside `Per_Number_Hourly_Limit__c`
  (default 50/hour, well under starter 10DLC daily tiers even at 24h).
- **Quiet hours fail closed**: 8am-9pm local from `Quiet_Hours_Timezone__c`.
  Apex's `TimeZone.getTimeZone` silently returns GMT for unknown ids, so the
  service rejects any id that does not round-trip — blank, invalid, or
  unrecognized timezones never send.
- **Flow -> Queueable**: the schedule-triggered Flow (daily, Leads with
  `Outreach_Status__c = 'Ready'`) calls an invocable that enqueues a Queueable
  with `Database.AllowsCallouts`. Callouts are capped at 90/run (platform limit
  100); overflow leads return `deferred` and remain Ready for the next run.
  Daily is a Flow scheduling limitation — sub-daily cadence needs Scheduled
  Apex later.
- **Every attempt is logged** to Outreach_Message__c: successes with the Twilio
  SID in `Provider_Message_Id__c`, failures with `Status__c='Failed'` and the
  error in `Send_Error__c`. Failed sends leave the lead Ready.
- **Callback idempotency**: the Apex REST endpoint (`/twilio/status`) updates
  `Delivery_Status__c` keyed on `Provider_Message_Id__c`; unknown SIDs insert
  exactly one stub (the unique external ID breaks concurrent-retry races).
  `Delivery_Status__c` is Text, not a restricted picklist, so a novel provider
  status can never fail the webhook.
- **Deferred before production**: Twilio `X-Twilio-Signature` validation on the
  callback endpoint (the auth token is locked inside the Named Credential and
  unavailable to Apex for HMAC), and first-touch template selection is
  name-based (`Name='First Touch'`, highest Approved version) pending the
  template-selection design.

### Consequences

No credential material exists in code, metadata, or Custom Settings. The
throttle is a metadata change, not a deploy. A misconfigured timezone can only
under-send, never violate quiet hours. The callback endpoint must be fronted by
a Site/Experience guest user at org-setup time, and signature validation must
land before that endpoint is exposed publicly.

## ADR-014: Inbound webhook — signature key storage, unmatched queue, no classifier

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The inbound path must validate X-Twilio-Signature on every request, but HMAC
validation needs the signing key readable by Apex — Named Credentials cannot
serve key material, and CLAUDE.md forbids secrets in code or Custom Settings.
Sites and guest profiles are org-bound and cannot fully ship as metadata.

### Decision

- **Signing key in protected custom metadata** (`Outreach_Secret__mdt`): the
  platform-sanctioned store that is neither code nor Custom Settings. The
  validator fails closed — blank token or blank
  `Outreach_Setting__mdt.Webhook_Base_Url__c` rejects every request with 403.
  The callout auth token itself still lives only in the Named Credential; the
  same value is duplicated into the protected CMT purely as the HMAC key.
  Signature enforcement now also covers the status callback, closing the
  ADR-013 deferred item.
- **Shared plumbing** in `TwilioWebhook`: signature check, form parsing, and
  US-region E.164 phone normalization mirroring `src/cleansing/normalize.py`.
- **Idempotent inserts** on `Provider_Message_Id__c` (query-first plus
  unique-index race fallback); a provider retry can never double-log.
- **Unmatched senders are queued, never dropped**: the message is inserted
  with `Unmatched__c = true` and no Lead; reviewers work that flag. Matching
  is exact on normalized E.164 against `Lead.Phone` (the sync writes E.164);
  multiple matches take the most recently created Lead.
- **Matched leads** get `Outreach_Status__c = 'Replied'` and `Last_Reply_At__c`
  stamped; the endpoint answers empty TwiML so Twilio does not error.
- **No classifier call** — intentional: replies are answered manually to
  generate labeled training data.
- **Site setup and live-phone verification are a runbook**
  (`docs/RUNBOOK_inbound.md`): Site domains and guest profiles are org
  configuration; the acceptance's live inbound test runs post-deploy per the
  runbook.

### Consequences

Webhooks are authenticated everywhere with zero fail-open paths, at the cost
of the auth token existing in two places (Named Credential for callouts,
protected CMT for HMAC) — rotating it means updating both. Manually created
leads with non-E.164 phones will land in the unmatched queue until phone
hygiene or fuzzier matching improves.

## ADR-015: Guardrail matching semantics — whole-message opt-out, bounded fuzz

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The opt-out invariant demands case-insensitive, punctuation-stripped,
edit-distance-1 fuzzy keyword matching — but naive token matching suppresses
interested prospects ("stop by our office next week"), and naive fuzzing makes
"and" match "END". False-positive suppression is a real cost.

### Decision

Guardrails are pure string functions (`src/guardrails/`), no ML, evaluated in
the fixed order opt_out -> legal_escalation -> hostility ->
bounce_or_autoreply -> loop_breaker -> proceed:

- **Opt-out matches the WHOLE message** (carrier STOP semantics), after
  canonicalization: NFKC + casefold + explicit unicode-confusables fold
  (Cyrillic/Greek lookalikes, reviewable table) + zero-width strip +
  punctuation collapsed + whitespace-insensitive comparison. Fuzzy matching
  (Levenshtein <= 1) applies only to keywords of length >= 4, so 3-letter
  keywords like END match exactly ("and" never suppresses).
- **Imperative stop-phrases** (`^(please )?stop
  (texting|sending|messaging|contacting|emailing|calling)`) also suppress —
  "stop texting me" is an opt-out even though it is not a bare keyword;
  negations ("please don't stop sending these") cannot match the anchor.
- **Legal escalation before hostility**: a profane lawyer threat is legal, not
  hostility. Legal terms use token-prefix matching (harass/harassment/
  harassing); "report you" is a phrase match.
- **Bounce/auto-reply**: OOO and auto-submitted markers match on both
  channels; DSN codes (5.x.y) and SMTP 55x codes are email-only signals.
- **Loop breaker last**: >= 2 outbound auto-replies in the thread forces human
  review — but an opt-out in a looping thread still suppresses (order matters).
- Hostility keyword list is deterministic and deliberately small; it will grow
  by appending reviewed terms, never by inference.

### Consequences

"stop by our office" and "please don't stop sending these" can never be
suppressed; "stop", "sto p", "stopp", and Cyrillic "ѕtop" always are.
Whole-message semantics mean opt-out sentences that neither match a keyword
nor start with an imperative stop-phrase flow to later rules — the classifier
and human queue are the backstop for those. Every result carries the rule and
trigger for reconstructability.

## ADR-016: Messaging-provider adapter — TextTorrent behind IMessagingProvider

- **Date:** 2026-07-30
- **Status:** Accepted

### Context

The send (WO-07) and inbound (WO-08) paths were built Twilio-specific; the
actual vendor is TextTorrent. The refactor work order references
docs/PROVIDER.md — which is not in the repo — and requires that every existing
WO-07/WO-08 Apex test pass unchanged.

### Decision

- `IMessagingProvider` (send -> ProviderResult{messageId, status, errorCode};
  validateInbound -> Boolean) is the only surface the send service and both
  REST endpoints touch, resolved via the `MessagingProvider` factory
  (@TestVisible injectable). The send-gate order (suppression -> quiet hours ->
  rate limit -> send -> log) is byte-for-byte untouched; only the callout call
  site changed.
- `TextTorrentProvider` quarantines ALL vendor wire details: auth via the
  `TextTorrent` Named Credential (the Twilio one is deleted; still no keys in
  Apex or Custom Settings), JSON send payload, response parsing, and webhook
  validation (delegating to the existing HMAC-SHA1 URL+sorted-params check;
  signature header `X-TextTorrent-Signature` with the legacy Twilio header
  name as fallback).
- **PROVIDER.md was absent**, so the wire contract is pinned by the existing
  test suites instead: 2xx + JSON with the message id under `sid` (fallbacks
  `id`, `message_id`) and provider status under `status`. The message id lands
  in `Provider_Message_Id__c` (still External ID + Unique — the idempotency
  key). When PROVIDER.md lands, reconcile TextTorrentProvider (and item 3's
  validation scheme) in that one class.
- Test-pinned names stay for now: the `TwilioWebhook` util class, the
  `Twilio*Rest` endpoint class names, and `Outreach_Secret__mdt.
  Twilio_Auth_Token__c` (now the provider signing key). Renaming them requires
  editing tests, which this work order forbids; a follow-up rename WO can do
  it wholesale.

### Consequences

Swapping vendors is one new class plus a factory line. The Twilio-era names in
tests/utilities are cosmetic debt, listed above so nobody mistakes them for
live Twilio coupling. The unverified TextTorrent endpoint path and payload
shape are a deploy blocker until PROVIDER.md (or vendor docs) confirms them.

## ADR-017: TextTorrent verified-contract campaign — polling-only API, inbound-freshness pager

### Context

The provider-contract work order requires capturing TextTorrent's real wire
contract with live credentials and reconciling `TextTorrentProvider` (ADR-016
had pinned it to test-suite guesses). The full official API reference was
fetched on 2026-07-30 and contradicts several assumptions:

- Base URL is `https://api.texttorrent.com/api/v1`; auth is `X-API-SID` +
  `X-API-PUBLIC-KEY` headers (not Basic/Password); 60 requests/min shared.
- Send is two-step (`POST /inbox/chat/create` then multipart
  `POST /inbox/chat` with a mandatory `chat_id`); the message id is integer
  `data.id`.
- **No webhooks, no signature scheme, no status callbacks exist anywhere in
  the documented API.** Inbound and delivery status are polled
  (`GET /inbox`, `GET /inbox/{chat_id}`); fetching a chat marks it read.
- Sends are "automatically cleaned using AI" server-side (the Conflict-A
  rewriter, on the API path), and an AI reply-generation endpoint exists
  (`/inbox/generate/ai/response`) which is never to be called (anti-goal:
  no LLM in the reply path).

Live credentials were not found on this machine or in Secrets Manager, so no
captures have run yet.

### Decision

- `docs/PROVIDER.md` is committed as a **docs-derived draft** with per-section
  `Verified:` markers; provider Apex is deliberately NOT reconciled until
  fixtures exist (one churn against evidence, not two against hearsay). The
  Twilio signature fallback stays until the inbound-transport decision — its
  removal is part of that change, not a standalone edit.
- `research/texttorrent_capture.py` is the evidence generator. Raw captures
  default to a directory outside the repo (real numbers and message content;
  the editor's auto-commit has already pushed PII once). Only sanitized
  fixtures are committed, with the sanitization mapping documented.
- The **inbound-freshness pager** ships now because it is transport-agnostic:
  `salesforce.inbound_gap` runs every 15 minutes, queries the system of
  record for the newest inbound `Outreach_Message__c`, and emits
  `Outreach/Inbound InboundGapBusinessSeconds`; the observability stack alarms
  at >= 4 business hours, treats missing data as breaching (covers the
  detector itself dying and the no-inbound-ever state), and pages via a new
  SNS topic (email: ydias@fundmatellc.com — the drift alarm now pages there
  too). Business hours are Mon-Fri 08:00-21:00 America/New_York by default,
  matching the send window so nights and weekends cannot page; timezone and
  hours are env-configurable.
- Known limitation, accepted for v1: the pager assumes outreach keeps
  generating replies during business hours. A deliberate campaign pause will
  page after 4 business hours. Preferred over silent reply loss; revisit by
  gating on recent outbound volume if it becomes noisy.

### Update (2026-07-30, after live credential access)

- **F1 (inbound signature validation) is closed, differently than assumed:
  there is no signature scheme to validate because there is no webhook.**
  Nothing inbound ever calls our endpoints under this vendor. The control
  that replaces signature validation is authenticated outbound polling plus
  the inbound-freshness pager; the WO-08 Apex REST inbound endpoint and the
  Twilio signature fallback are deleted in the same commit as the poller.
- Credentials were found in the legacy `textTorrenttoSf` Lambda's environment
  variables (`TT_SID` / `TT_PUBLIC_KEY`; its IAM role has no Secrets Manager
  grants at all) and normalized into the `outreach/texttorrent` secret
  (`{api_sid, public_key}`). Our code reads only the new secret; the legacy
  function belongs to another project and was left untouched.
- Live-verified: the header pair authenticates; `/user/auth/me` is POST (the
  docs imply GET); the account's real limits are 500 requests/min and 10,000
  SMS/day (the documented 60 rpm is the generic tier); TextTorrent enforces
  its own 06:00-22:00 America/New_York send window
  (`message_time_restriction`); the vendor blocked list holds ~10,487 numbers.
- **Inbound transport approved: Option A** — an AWS poller Lambda upserting
  inbound messages into Salesforce on `Provider_Message_Id__c` (idempotency
  enforced at the database layer via External ID + Unique). The poll
  cursor/watermark lives in SSM Parameter Store, not Salesforce; SSM over
  DynamoDB keeps the "no DynamoDB" anti-goal intact — the cursor is transport
  bookkeeping, not live state. The poll interval awaits sign-off on the
  latency/budget proposal; the poller is not built until then.
- Conflicts A and B are settled in ADR-018 and ADR-019.

### Still pending

- The capture fixtures in PROVIDER.md §6 (real send + reply from the test
  phone, delivery-status integer semantics) and the provider-code
  reconciliation they gate.
- The dashboard-UI webhook check (owner is doing this personally).

## ADR-018: Conflict A — vendor AI message rewriter: disable, then verify daily

### Context

TextTorrent "automatically clean[s] messages using AI to fix encoding issues"
on the API send path itself (docs 5.9), and separately offers AI reply
generation (5.11). Any in-flight mutation of an approved template defeats
template immutability (only Approved, versioned templates are sendable) and
makes the audit trail lie about what was actually sent. No API toggle for the
send-path cleaner is documented.

### Decision

- Request written, account-wide disablement from Dev@texttorrent.com — draft
  at docs/vendor/ai-rewriter-disable-request.md; the written reply gets filed
  next to it. Vendor claims are treated as unverified until the canary agrees.
- Trust is continuous, not one-time: the daily canary Lambda
  (`texttorrent.canary`) sends rewriter-tempting text (mixed case, em dash,
  doubled quotes and exclamations, stray symbols, a hard line break) to the
  company-controlled test number, reads the stored message back from the API,
  and emits `Outreach/TextTorrent CanaryByteIdentical`. The alarm pages on
  anything but a daily 1 — a missing run and a mutated message are the same
  incident (missing data is breaching). A vendor can re-enable a feature in a
  release; we find out the same day.
- The AI reply-generation endpoint is never called (anti-goal: no LLM in the
  reply path). The client deliberately has no method for it.
- Accepted limitation: API read-back proves server-side storage fidelity, not
  carrier-path fidelity. The one-time live acceptance
  (docs/RUNBOOK_texttorrent.md) compares the handset-received text
  byte-for-byte; ongoing carrier-path drift is out of scope until evidence
  says otherwise.

### Consequences

Cost is one SMS per day. If the vendor confirms disablement in writing, the
canary still runs — the confirmation dates a promise; the canary tests the
present.

## ADR-019: Conflict B — opt-out authority: ours is authoritative, theirs is a backstop

### Context

Two suppression systems exist: our append-only store (ADR-009, the compliance
control) and TextTorrent's opt-out-word engine feeding a blocked list (10,487
entries at adoption). They WILL diverge: their engine auto-blocks replies our
pipeline could miss, and our cross-channel suppressions never reach them on
their own.

### Decision

- **Our suppression store is the single source of truth.** TextTorrent's
  blocked list is a backstop we reconcile against, never the authority.
- Their auto-blocking CAN be effectively disabled (opt-out words are
  deletable via API, docs 4.9.4) but deliberately is NOT disabled yet: until
  our reply pipeline is live end to end, their engine is the only thing
  catching STOP replies in real time. Consolidating to one enforcement point
  is revisited once the inbound poller and guardrails are in production.
- Nightly bidirectional reconciliation (`texttorrent.reconcile`):
  - In theirs, not ours -> added to our store immediately (source
    `texttorrent_reconciliation`) — an opt-out we missed.
  - In ours, not theirs -> pushed to their blocked list (SMS identifiers
    only; emails are not pushed to an SMS vendor).
  - Unparseable vendor entries count as divergence — a human must look.
  - `Outreach/Suppression OptOutDivergence` alarms at > 0 with missing-data
    breaching: zero is the only acceptable number, and a job that did not run
    pages too.
- First production run imports the existing vendor blocked list (~10.5k
  numbers) into our store and alarms once. That alarm is correct — those are
  opt-outs our store does not have. Imported entries carry the reconciliation
  source so provenance is never confused with a first-party STOP.
- Fallback: `{"export_key": ...}` reconciles from a dropped vendor CSV export
  if the API pull is ever unavailable.

### Consequences

An opt-out entered on either side is enforced on both within 24 hours, and
any disagreement pages a human. Live acceptance per docs/RUNBOOK_texttorrent.md:
opt a number out through the TextTorrent UI only and watch the job catch it,
add it, and alarm.

## ADR-020: Apex guardrails port — truth-table contract and the opt-out mirror

(The work order that produced this numbered it "ADR-019"; 019 was already
taken by the Conflict-B decision, so it lands here unchanged in substance.)

### Context

src/guardrails/ (ADR-015) is Python; the inbound endpoint is Apex. They never
met, so an inbound STOP did nothing to our suppression list. Hard constraint:
guardrails must not depend on a network call — an opt-out control that
requires AWS to be reachable is a compliance control with an availability
dependency. Calling the classify endpoint was explicitly ruled out.

### Decision

- `OutreachGuardrails.cls` ports ADR-015's semantics exactly (fixed rule
  order; whole-message opt-out after fold + punctuation collapse; Levenshtein
  <= 1 only on keywords of length >= 4; anchored imperative stop-phrases with
  negations unable to match; legal before hostility). The Python
  implementation remains the reference and the training-time evaluator.
- **The truth table is the shared contract**:
  `tests/fixtures/guardrail_truth_table.csv` (71 rows, generated by running
  the reference implementation over the ADR-015 adversarial suite),
  duplicated byte-identically as the `Guardrail_Truth_Table` static resource.
  CI checks it three ways: pytest runs every row against Python,
  RunLocalTests runs every row against Apex, and a pytest asserts the two CSV
  copies are byte-identical. New adversarial cases go into the CSV, never
  into one language's tests.
- Apex has no NFKC API. `fold()` implements the compatibility subset the
  rules rely on (fullwidth→ASCII, NBSP/ideographic space, zero-width strip)
  plus the same explicit confusables table; `toLowerCase` stands in for
  casefold. Any real-world gap surfaces as a truth-table disagreement.
- Wiring order in TwilioInboundRest is fixed: signature check -> idempotent
  insert -> guardrails -> persist verdict (`Guardrail_Action__c`,
  `Guardrail_Rule__c`). Guardrails run after the insert so a provider retry
  short-circuits at the idempotency check and can never re-run side effects.
- `suppress_and_stop` writes BOTH stores:
  - **Salesforce synchronously, in the webhook transaction**: `Opted_Out__c`
    (the send gate's field), `DoNotCall`, `HasOptedOutOfEmail`,
    `Opt_Out_At__c`, `Opt_Out_Source__c`, `Outreach_Status__c = Suppressed`.
    Enforcement never waits on a network call.
  - **AWS asynchronously** via `OutreachSuppressionSyncQueueable` ->
    `POST /suppressions` on the inference stack (IAM SigV4 through the
    `AWS_Suppression` Named Credential; caller identity is the CDK-managed
    `salesforce-suppression-caller` user scoped to that one method) ->
    `SuppressionStore.add_suppression`. Mechanism chosen over a direct
    in-transaction callout (Apex forbids callouts after DML, and the write
    path must not block on AWS) and over Platform Events/EventBridge
    (settled as a later option, not v1).
  - Failure behaviour: the normal case lands in seconds, inside ADR-009's
    60-second bound. Through 5 retries with growing backoff, AWS-unreachable
    ends with the source message flagged `Needs_Review__c` and the error in
    `Send_Error__c` — a human closes the loop; the Salesforce gate is closed
    either way. An unmatched sender still mirrors (no Lead is required to
    honor an opt-out); a sender number that cannot be normalized flags for
    review instead.
- `human_review` sets `Needs_Review__c`; no automated response of any kind.

### Consequences

An inbound STOP closes the send gate the moment the webhook transaction
commits, with or without AWS. Both guardrail implementations must agree on
every truth-table row for CI to pass. When the inbound poller (ADR-017,
Option A) replaces the webhook, the guardrail invocation moves with the
insert point — the class and the truth table are transport-agnostic.

## ADR-022: Final reply-intent taxonomy — 13 classes, reconciled against real data

(The work order that produced this numbered it "ADR-018"; 018 and 019 were
already taken by the Conflict-A/B decisions, and 020/021 by the guardrail port
and pending throughput work. Substance unchanged.)

### Context

ADR-011's 12 intents were explicitly provisional, to be reconciled before real
data existed. Real data now exists: the TextTorrent conversation export —
which turned out to carry **no labels at all** (it is a raw message export;
156,251 rows, 39,353 inbound replies, 28,047 distinct). The reconciliation is
therefore empirical: the production guardrails plus deterministic pattern
buckets were run over every distinct inbound reply to size candidate classes
(estimates, not labels — docs/TAXONOMY.md documents the method and numbers).

### Decision

- Final set (13), updated in the `Outreach_Intent` Global Value Set BEFORE any
  labeling or training, because picklist API names become load-bearing after:
  Interested, Amount_Given, Question, Request_More_Info, Call_Request,
  Process_Update, Not_Interested, Wrong_Person, Hostile, Opt_Out,
  Legal_Escalation, Auto_Reply, Unclear.
- Changes from ADR-011: `Not_Now` (75 unique < 150) and `Already_Financed`
  (78 < 150) merged into Not_Interested; `Referral` (34 < 150, heterogeneous)
  removed; `Amount_Given` (569 unique), `Call_Request` (358),
  `Process_Update` (859), and `Hostile` (553) added on observed volume.
- Opt_Out and Legal_Escalation stay per ADR-011's reasoning — guardrail-
  detected, never ML-predicted, one consistent label on records. Auto_Reply
  and the new Hostile are kept under the same rule; their sub-150 sample
  counts are irrelevant because the classifier never has to learn them.
- Auto-reply eligibility per class, with reasoning, is in TAXONOMY.md §5:
  eligible = Interested, Amount_Given, Request_More_Info, Not_Interested,
  Wrong_Person; everything else routes to a human in v1 (Question included,
  until template coverage is proven).
- TAXONOMY.md §6 is the labeling guide: definitions, real examples, boundary
  cases, and precedence — it is what determines whether two humans agree.
- Open questions recorded, not guessed (TAXONOMY.md §3): the plan document's
  taxonomy remains unavailable (OQ-1); channel-switch requests as SMS
  preference revocation (OQ-3); wrong-number auto-suppression (OQ-4); the
  suspiciously low STOP volume implying carrier-level interception (OQ-5).

### Consequences

Labeling can start against a stable value set and a written guide. Two classes
that would have starved (< 150 examples) no longer exist to starve. The
scratch-org deploy of the value set is validated by the existing CI job; if
the plan document's taxonomy surfaces and disagrees, that is a new ADR, not an
edit to this one.

## ADR-021: Send throughput — hourly Batch Apex replaces the daily Flow

### Context

ADR-013's daily schedule-triggered Flow plus one Queueable capped at 90
callouts yielded ~90 sends/day against a 50,000/month plan (F4). Meanwhile the
real vendor limits are now known (ADR-017 update): 500 API requests/min,
**10,000 SMS/day account cap**, a vendor-side 06:00-22:00 ET send window, and
100+ active sender numbers.

### Decision

- **Batch Apex over chained Queueables.** `OutreachSendBatch`
  (`Database.AllowsCallouts`, `Database.Stateful`) walks the whole Ready
  backlog; every `execute()` is its own transaction with a fresh 100-callout
  budget, so scope = `OutreachSendService.MAX_CALLOUTS_PER_RUN` (90) chunks
  clear any backlog in one run. Chained Queueables offer the same callout
  budget but require hand-rolled chain bookkeeping, give no built-in
  progress/abort visibility, and can fan out concurrently; a batch is one
  serialized, observable job.
- **Hourly Scheduled Apex** (`OutreachSendScheduler`, cron `0 0 * * * ?`)
  replaces the Flow trigger — Flows cannot schedule sub-daily. Running 24/7 is
  safe: quiet hours, suppression, and the rate limit are enforced per-lead
  inside each chunk, unchanged from ADR-013 (the batch delegates to
  `OutreachSendService.sendBatch`; the ordering is untouched, and the
  rate-limit check re-queries the trailing hour every chunk so the throttle
  holds across chunk boundaries). The Flow, `OutreachSendInvocable`, and
  `OutreachSendQueueable` are deleted — dead code that looks live is worse
  than no code.
- **Ceilings, recomputed with real numbers.** Machinery: ~90 sends/transaction
  x unlimited chunks x 24 runs/day — not binding. Vendor API: 500 req/min —
  not binding at this scale. **Binding: min(TextTorrent 10,000/day account
  cap, Per_Number_Hourly_Limit__c x sending numbers x send-window hours).**
  The 10DLC tier itself is not visible through TextTorrent (SignalHouse brand
  ids exist on the account; the tier number needs a vendor answer) — until it
  is known, the throttle is the governing dial we control.
- **Per_Number_Hourly_Limit__c stays the throttle**, raised 50 → 200 in the
  Default record: sending currently uses a single from-number, and 50/hr was
  1,200/day even running 24h — under the 50k/month plan. 200/hr across the
  ~13h recipient-local send window ≈ 2,600/day sustained (~78k/month
  headroom); a 5,000-send day needs ~385/hr — set 400 for load tests or
  peaks. Revisit when the 10DLC tier is confirmed or multi-number rotation
  lands (the limit is per number by name, global in implementation, correct
  while exactly one number sends).
- **Load testing** uses `MockMessagingProvider`, double-gated behind
  `Outreach_Setting__mdt.Use_Mock_Provider__c` AND a sandbox/scratch org —
  production always resolves the real provider. A 5,000-message load test
  with real sends was deliberately NOT run from here: it would deliver 5,000
  real SMS through the production TextTorrent account (credits, carrier
  reputation, a real recipient). The scratch-org procedure is
  docs/RUNBOOK_load_test.md; a real-send variant is an explicit owner
  decision.

### Consequences

Throughput scales to the vendor cap by turning one dial, with every
compliance gate untouched and test-pinned inside the batch. The org needs a
one-time `OutreachSendScheduler.scheduleHourly()` registration (runbook).

---

## ADR-023: Classify service deployment — Function URL, in-function bearer auth

- **Date:** 2026-07-31
- **Status:** Accepted

### Context

The rules-v1 classify service (guardrails + TAXONOMY.md buckets, no trained
model) needs a stable HTTPS URL in the dev account. It is rules-only — no
heavy dependencies, so a plain zip suffices — and it must never send
messages or touch Salesforce. The work order left Function URL vs API
Gateway open, required the bearer token in Secrets Manager (not an env
var), and the handoff-intent list changeable without a redeploy.

### Decision

- **Lambda Function URL over API Gateway.** Auth is app-level bearer either
  way, so API Gateway adds cost and moving parts without adding control; a
  Function URL is one construct and a stable HTTPS URL. `AuthType: NONE` at
  the URL, with auth enforced in-function on every request (401 without a
  valid token, 503 if the token secret is unreachable — fail closed, never
  open). Revisit if the future Salesforce->classify callout wants the
  existing IAM-SigV4 API Gateway pattern instead (ADR-017 §sync callout);
  nothing here precludes moving.
- **Token generated server-side** by Secrets Manager
  (`outreach/classify/token`, 48 chars, `GenerateSecretString`): it never
  appears in code, template, terminal, or CDK context. The function caches
  it per execution environment; rotation lands on the next cold start.
- **The asset ships only `classifier/` + `guardrails/`** — the send path
  (`texttorrent/`), Salesforce clients, and pipeline code are excluded from
  the zip, and the role reads exactly one secret. "No send capability" is
  enforced by what is deployed, not by convention.
- **`CLASSIFY_HANDOFF_INTENTS` is a plain env var** (comma list), so the
  handoff set changes without a redeploy. It participates in the /health
  rule-set hash, so instances with different lists are distinguishable.
- **No provisioned concurrency**; cold starts are acceptable at zero
  traffic.

### Consequences

A stable HTTPS endpoint with one secret, one function, no gateway. Anyone
holding the URL can *reach* the function (auth is in-function), so the
bearer check runs first on every path, including /health. The adapter
(`classifier/lambda_api.py`) mirrors research/serve_local.py: SHA-256-only
logging, fail-closed 200s. Bodies never hit CloudWatch.

### Update (2026-07-31, Interested bucket)

- The Interested bucket now also matches any message containing
  `interested` that did not already match the decline check, which runs
  first (`yes im interested` previously fell to Unclear -> no_action, the
  same failure class as the Process_Update gap). Decline phrasing still
  wins: `not interested at all` stays Not_Interested. Rule-set hash
  changed accordingly.
- The Part D voice-analysis interest-per-send rates were computed with the
  narrower pre-change bucket: treat them as lower bounds.
