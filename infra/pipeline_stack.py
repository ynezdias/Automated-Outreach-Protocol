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
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from infra.layers import dependencies_layer

RAW_ENRICHMENT_PREFIX = "raw/enrichment/"
SALESFORCE_SECRET_NAME = "outreach/salesforce/jwt"  # pragma: allowlist secret
TEXTTORRENT_SECRET_NAME = "outreach/texttorrent"  # pragma: allowlist secret
SYNC_HANDLER = "salesforce.sync.handler"
RECONCILE_HANDLER = "salesforce.reconcile.handler"
INBOUND_GAP_HANDLER = "salesforce.inbound_gap.handler"
CANARY_HANDLER = "texttorrent.canary.handler"
SUPPRESSION_RECONCILE_HANDLER = "texttorrent.reconcile.handler"

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

        salesforce_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "SalesforceJwtSecret", SALESFORCE_SECRET_NAME
        )
        texttorrent_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "TextTorrentSecret", TEXTTORRENT_SECRET_NAME
        )
        layer = dependencies_layer(self, "DependenciesLayer")

        def step_function(name: str, handler: str, *, timeout_minutes: int = 5) -> lambda_.Function:
            function = lambda_.Function(
                self,
                f"{name}Fn",
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                layers=[layer],
                code=lambda_.Code.from_asset(_SRC_PATH),
                handler=handler,
                timeout=Duration.minutes(timeout_minutes),
                memory_size=1024,
                environment={
                    "DATA_BUCKET": data_bucket.bucket_name,
                    "COOLDOWN_DAYS": "90",
                    # Twilio Lookup costs $0.01/query — off until a human enables it.
                    "TWILIO_LOOKUP_ENABLED": "false",
                    "SALESFORCE_SECRET_ID": SALESFORCE_SECRET_NAME,
                    "TEXTTORRENT_SECRET_ID": TEXTTORRENT_SECRET_NAME,
                    # Company-controlled test phone; the daily canary texts it.
                    "TEXTTORRENT_CANARY_TO": "+15512357742",
                },
            )
            data_bucket.grant_read_write(function)
            return function

        invokes: list[tasks.LambdaInvoke] = []
        for name, module in STEPS:
            invokes.append(
                tasks.LambdaInvoke(
                    self,
                    f"{name}Task",
                    lambda_function=step_function(name, f"{module}.handler"),
                    payload_response_only=True,
                )
            )

        # Final state: push the cleansed batch to Salesforce (Bulk API 2.0 upsert).
        metrics_policy = iam.PolicyStatement(actions=["cloudwatch:PutMetricData"], resources=["*"])
        sync_fn = step_function("SyncToSalesforce", SYNC_HANDLER, timeout_minutes=15)
        salesforce_secret.grant_read(sync_fn)
        sync_fn.add_to_role_policy(metrics_policy)
        invokes.append(
            tasks.LambdaInvoke(
                self, "SyncToSalesforceTask", lambda_function=sync_fn, payload_response_only=True
            )
        )

        chain = sfn.Chain.start(invokes[0])
        for invoke in invokes[1:]:
            chain = chain.next(invoke)

        # Daily reconciliation: lake vs Salesforce counts; alarmed in observability.
        reconcile_fn = step_function("Reconcile", RECONCILE_HANDLER, timeout_minutes=15)
        salesforce_secret.grant_read(reconcile_fn)
        reconcile_fn.add_to_role_policy(metrics_policy)
        events.Rule(
            self,
            "DailyReconciliation",
            schedule=events.Schedule.rate(Duration.days(1)),
            targets=[targets.LambdaFunction(reconcile_fn)],
        )

        # Inbound-freshness detector: emits the business-hours gap since the
        # newest inbound message every 15 minutes; observability alarms at 4h.
        inbound_gap_fn = step_function("InboundGap", INBOUND_GAP_HANDLER)
        salesforce_secret.grant_read(inbound_gap_fn)
        inbound_gap_fn.add_to_role_policy(metrics_policy)
        events.Rule(
            self,
            "InboundGapSchedule",
            schedule=events.Schedule.rate(Duration.minutes(15)),
            targets=[targets.LambdaFunction(inbound_gap_fn)],
        )

        # Daily AI-rewriter canary (Conflict A): byte-identity of sent text.
        canary_fn = step_function("RewriteCanary", CANARY_HANDLER)
        texttorrent_secret.grant_read(canary_fn)
        canary_fn.add_to_role_policy(metrics_policy)
        events.Rule(
            self,
            "DailyRewriteCanary",
            schedule=events.Schedule.rate(Duration.days(1)),
            targets=[targets.LambdaFunction(canary_fn)],
        )

        # Nightly opt-out reconciliation (Conflict B): zero-divergence check
        # between our suppression store and the vendor blocked list.
        suppression_reconcile_fn = step_function(
            "SuppressionReconcile", SUPPRESSION_RECONCILE_HANDLER, timeout_minutes=15
        )
        texttorrent_secret.grant_read(suppression_reconcile_fn)
        suppression_reconcile_fn.add_to_role_policy(metrics_policy)
        events.Rule(
            self,
            "NightlySuppressionReconciliation",
            schedule=events.Schedule.rate(Duration.days(1)),
            targets=[targets.LambdaFunction(suppression_reconcile_fn)],
        )
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
