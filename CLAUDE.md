# Automated Outreach Protocol

Automated outreach system built on AWS (CDK) with a Salesforce integration. This repo
is currently a skeleton — no application code yet.

## Toolchain

- Python 3.12, managed with **uv** (`uv sync` to install)
- **Ruff** for lint + format, **mypy --strict** for types, **pytest** for tests
- **AWS CDK v2 (Python)** under `infra/`, four stacks: data, pipeline, inference,
  observability
- **SFDX** project under `salesforce/` with scratch org definition
- **GitHub Actions** CI; **pre-commit** with Ruff and detect-secrets

## Commands

- `make check` — lint, typecheck, tests (the acceptance gate)
- `make fmt` — auto-fix lint and formatting
- `cdk synth` — synthesize the four stacks (requires Node + `npm i -g aws-cdk`)
- `uv run pre-commit install` — enable git hooks (run once per clone)

## Layout

- `infra/app.py` — CDK entry point (`build_app()`); `infra/stacks/` — one module per stack
- `tests/` — pytest suite
- `salesforce/` — SFDX project (`force-app/` metadata, `config/project-scratch-def.json`)
- `docs/DECISIONS.md` — ADRs; record new architectural decisions there

## Conventions

- All Python is fully typed (mypy strict covers `infra/` and `tests/`)
- New architectural decisions get an ADR before or with the implementing PR
- Secrets never enter the repo; detect-secrets baseline is `.secrets.baseline`
