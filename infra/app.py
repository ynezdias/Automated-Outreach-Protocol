"""CDK app entry point."""

import aws_cdk as cdk

from infra.data_stack import DataStack
from infra.inference_stack import InferenceStack
from infra.observability_stack import ObservabilityStack
from infra.pipeline_stack import PipelineStack


def build_app() -> cdk.App:
    """Construct the CDK app with all stacks."""
    app = cdk.App()
    data = DataStack(app, "OutreachDataStack")
    PipelineStack(app, "OutreachPipelineStack", data_bucket=data.data_bucket)
    InferenceStack(app, "OutreachInferenceStack", data_bucket=data.data_bucket)
    ObservabilityStack(app, "OutreachObservabilityStack")
    return app


if __name__ == "__main__":
    build_app().synth()
