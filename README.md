# Automated-Outreach-Protocol

Automated outreach system built on AWS (CDK, Python) with a Salesforce integration.
Currently a repository skeleton — see [CLAUDE.md](CLAUDE.md) for conventions and
[docs/DECISIONS.md](docs/DECISIONS.md) for architecture decisions.

## Setup

```sh
uv sync                      # install Python 3.12 deps (requires uv)
uv run pre-commit install    # enable git hooks (ruff + detect-secrets)
```

## Development

```sh
make check    # lint (ruff), typecheck (mypy --strict), unit tests (pytest)
make fmt      # auto-fix lint and formatting
cdk synth     # synthesize the four CDK stacks (requires Node.js + npm i -g aws-cdk)
```

## Layout

- `infra/` — AWS CDK app; stacks: data, pipeline, inference, observability
- `salesforce/` — SFDX project with scratch org definition
- `tests/` — pytest suite
- `docs/DECISIONS.md` — ADRs
- `.github/workflows/ci.yml` — CI: lint, typecheck, tests, cdk synth, Salesforce
  scratch org validation (requires the `SFDX_AUTH_URL` repo secret; self-skips if absent)
