"""Shared Lambda dependencies layer (the ADR-010 bundling item).

``build/layer/python`` is produced by ``make layer``::

    uv pip install --target build/layer/python \
        --python-platform aarch64-manylinux2014 --python-version 3.12 \
        --no-build polars phonenumbers dnspython rsa tzdata

polars is a compiled dependency, so the wheels are resolved for the Lambda
runtime's architecture (aarch64 manylinux — all functions run ARM_64), never
for the Windows-ARM64 dev machine. uv's cross-platform resolution makes Docker
unnecessary.

At synth time a placeholder is created if the build hasn't run, so tests and
CI synth keep working; deploying a placeholder layer fails loudly at first
invoke (ImportError), never silently.
"""

from pathlib import Path

from aws_cdk import aws_lambda as lambda_
from constructs import Construct

LAYER_SOURCE = Path(__file__).resolve().parent.parent / "build" / "layer"

#: Everything the deployed handlers import beyond the stdlib. rsa and tzdata
#: were missing from the original ADR-010 list but salesforce.auth and
#: salesforce.inbound_gap import them.
LAYER_DEPENDENCIES = ("polars", "phonenumbers", "dnspython", "rsa", "tzdata")


def dependencies_layer(scope: Construct, construct_id: str) -> lambda_.LayerVersion:
    python_dir = LAYER_SOURCE / "python"
    python_dir.mkdir(parents=True, exist_ok=True)
    if not any(python_dir.iterdir()):
        (python_dir / ".placeholder").write_text(
            "empty placeholder — run `make layer` before deploying (ADR-010)\n"
        )
    return lambda_.LayerVersion(
        scope,
        construct_id,
        code=lambda_.Code.from_asset(str(LAYER_SOURCE)),
        compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
        compatible_architectures=[lambda_.Architecture.ARM_64],
        description="polars, phonenumbers, dnspython, rsa, tzdata (aarch64)",
    )
