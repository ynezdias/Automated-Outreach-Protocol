"""CDK app entry point."""

import aws_cdk as cdk

from infra.stacks import DataStack, InferenceStack, ObservabilityStack, PipelineStack


def build_app() -> cdk.App:
    """Construct the CDK app with all stacks."""
    app = cdk.App()
    DataStack(app, "OutreachDataStack")
    PipelineStack(app, "OutreachPipelineStack")
    InferenceStack(app, "OutreachInferenceStack")
    ObservabilityStack(app, "OutreachObservabilityStack")
    return app


if __name__ == "__main__":
    build_app().synth()
