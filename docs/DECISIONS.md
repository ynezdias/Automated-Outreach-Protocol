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
