"""Unit tests for the pipeline stack."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.data_stack import DataStack
from infra.pipeline_stack import RECONCILE_HANDLER, STEPS, SYNC_HANDLER, PipelineStack


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    data = DataStack(app, "TestDataStack")
    return Template.from_stack(
        PipelineStack(app, "TestPipelineStack", data_bucket=data.data_bucket)
    )


def test_one_lambda_per_step_plus_sync_and_reconcile(template: Template) -> None:
    template.resource_count_is("AWS::Lambda::Function", len(STEPS) + 2)
    functions = template.find_resources("AWS::Lambda::Function")
    handlers = {fn["Properties"]["Handler"] for fn in functions.values()}
    assert handlers == {f"{module}.handler" for _, module in STEPS} | {
        SYNC_HANDLER,
        RECONCILE_HANDLER,
    }


def test_daily_reconciliation_schedule(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({"ScheduleExpression": "rate(1 day)"}),
    )


def test_state_machine_chains_all_steps(template: Template) -> None:
    template.resource_count_is("AWS::StepFunctions::StateMachine", 1)


def test_trigger_rule_matches_raw_enrichment_prefix(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like(
            {
                "EventPattern": Match.object_like(
                    {
                        "source": ["aws.s3"],
                        "detail-type": ["Object Created"],
                        "detail": Match.object_like(
                            {"object": {"key": [{"prefix": "raw/enrichment/"}]}}
                        ),
                    }
                )
            }
        ),
    )


def test_twilio_lookup_disabled_by_default(template: Template) -> None:
    for fn in template.find_resources("AWS::Lambda::Function").values():
        env = fn["Properties"]["Environment"]["Variables"]
        assert env["TWILIO_LOOKUP_ENABLED"] == "false"
        assert env["COOLDOWN_DAYS"] == "90"
