"""Pipeline stack: Step Functions cleansing pipeline and its step Lambdas.

An EventBridge rule fires the state machine whenever an object is created under
``raw/enrichment/`` on the data bucket (which has EventBridge notifications
enabled). The state machine chains seven single-purpose Lambdas — one per
cleansing step, each independently invocable with the event shape documented in
its ``src/cleansing/steps/`` module (no monolithic Lambdas, per CLAUDE.md).

NOTE: Lambda code ships as a plain source asset (``src/``); third-party
dependency bundling (polars, phonenumbers, dnspython) is a deploy-time
follow-up tracked in ADR-010.
"""

from pathlib import Path
from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

RAW_ENRICHMENT_PREFIX = "raw/enrichment/"

#: Pipeline steps in execution order: (construct name, Lambda handler module).
STEPS = (
    ("SchemaValidate", "cleansing.steps.schema_validate"),
    ("Normalize", "cleansing.steps.normalize"),
    ("Validate", "cleansing.steps.validate"),
    ("Dedupe", "cleansing.steps.dedupe"),
    ("Suppress", "cleansing.steps.suppress"),
    ("AssignExternalId", "cleansing.steps.assign_external_id"),
    ("WriteStaging", "cleansing.steps.write_staging"),
)

_SRC_PATH = str(Path(__file__).resolve().parent.parent / "src")


class PipelineStack(Stack):
    """Step Functions state machine chaining the seven cleansing step Lambdas."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        invokes: list[tasks.LambdaInvoke] = []
        for name, module in STEPS:
            function = lambda_.Function(
                self,
                f"{name}Fn",
                runtime=lambda_.Runtime.PYTHON_3_12,
                code=lambda_.Code.from_asset(_SRC_PATH),
                handler=f"{module}.handler",
                timeout=Duration.minutes(5),
                memory_size=1024,
                environment={
                    "DATA_BUCKET": data_bucket.bucket_name,
                    "COOLDOWN_DAYS": "90",
                    # Twilio Lookup costs $0.01/query — off until a human enables it.
                    "TWILIO_LOOKUP_ENABLED": "false",
                },
            )
            data_bucket.grant_read_write(function)
            invokes.append(
                tasks.LambdaInvoke(
                    self, f"{name}Task", lambda_function=function, payload_response_only=True
                )
            )

        chain = sfn.Chain.start(invokes[0])
        for invoke in invokes[1:]:
            chain = chain.next(invoke)
        self.state_machine = sfn.StateMachine(
            self,
            "CleansingStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(chain),
            timeout=Duration.hours(2),
        )

        rule = events.Rule(
            self,
            "RawEnrichmentObjectCreated",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {"key": [{"prefix": RAW_ENRICHMENT_PREFIX}]},
                },
            ),
        )
        rule.add_target(
            targets.SfnStateMachine(
                self.state_machine,
                input=events.RuleTargetInput.from_object(
                    {
                        "bucket": events.EventField.from_path("$.detail.bucket.name"),
                        "key": events.EventField.from_path("$.detail.object.key"),
                    }
                ),
            )
        )
