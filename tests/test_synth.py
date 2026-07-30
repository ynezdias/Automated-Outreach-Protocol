"""Verify the CDK app synthesizes the expected stacks."""

from infra.app import build_app

EXPECTED_STACKS = {
    "OutreachDataStack",
    "OutreachPipelineStack",
    "OutreachInferenceStack",
    "OutreachObservabilityStack",
}


def test_app_synthesizes_four_stacks() -> None:
    assembly = build_app().synth()
    assert {stack.stack_name for stack in assembly.stacks} == EXPECTED_STACKS
