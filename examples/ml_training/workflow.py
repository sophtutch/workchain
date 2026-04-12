"""Build the ML model training workflow definition.

Dependency graph:
    prepare_dataset → split_train_test → train_model (async, times out)
                                              ↓
                                       evaluate_model → publish_model

train_model polls indefinitely (loss never converges) and hits the
15-second poll timeout, producing STEP_FAILED + POLL_TIMEOUT events.
evaluate_model and publish_model are never reached.
"""

from __future__ import annotations

from examples.ml_training import steps  # noqa: F401
from examples.ml_training.steps import DatasetConfig, SplitConfig, TrainConfig
from workchain import PollPolicy, Step, Workflow


def build_workflow(
    dataset_name: str = "imagenet-mini",
    sample_size: int = 10_000,
    model_type: str = "resnet50",
) -> Workflow:
    """Construct a 5-step ML training pipeline that fails via poll timeout."""
    return Workflow(
        name="ml_training",
        steps=[
            Step(
                name="prepare_dataset",
                handler="examples.ml_training.steps.prepare_dataset",
                config=DatasetConfig(dataset_name=dataset_name, sample_size=sample_size),
            ),
            Step(
                name="split_train_test",
                handler="examples.ml_training.steps.split_train_test",
                config=SplitConfig(train_ratio=0.8),
            ),
            Step(
                name="train_model",
                handler="examples.ml_training.steps.train_model",
                config=TrainConfig(model_type=model_type, epochs=100, learning_rate=0.001),
                is_async=True,
                completeness_check="examples.ml_training.steps.check_training",
                poll_policy=PollPolicy(
                    interval=3.0,
                    backoff_multiplier=1.0,
                    timeout=15.0,
                    max_polls=20,
                ),
            ),
            Step(
                name="evaluate_model",
                handler="examples.ml_training.steps.evaluate_model",
                depends_on=["train_model"],
            ),
            Step(
                name="publish_model",
                handler="examples.ml_training.steps.publish_model",
                depends_on=["train_model"],
            ),
        ],
    )
