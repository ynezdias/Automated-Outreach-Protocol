.PHONY: check lint typecheck test synth fmt install

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

synth:
	cdk synth

fmt:
	uv run ruff check --fix .
	uv run ruff format .
