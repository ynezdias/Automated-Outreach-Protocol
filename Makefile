.PHONY: check lint typecheck test synth fmt install layer

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

# Lambda dependencies for the ARM_64 runtime (ADR-010): cross-platform wheel
# resolution via uv — no Docker needed, and never the dev machine's platform.
layer:
	uv pip install --target build/layer/python \
		--python-platform aarch64-manylinux2014 --python-version 3.12 \
		--no-build polars phonenumbers dnspython rsa tzdata

fmt:
	uv run ruff check --fix .
	uv run ruff format .
