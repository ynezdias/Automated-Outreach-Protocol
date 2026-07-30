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
**inference**, and **observability**, defined under `infra/stacks/` and wired in
`infra/app.py`.

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
