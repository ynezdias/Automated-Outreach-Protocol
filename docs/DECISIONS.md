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
