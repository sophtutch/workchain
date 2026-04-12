"""Step handlers for the ML model training pipeline.

Steps:
  1. prepare_dataset     - Download and clean training data
  2. split_train_test    - Partition into train/test splits
  3. train_model         - Async: submit training job, polls but always times out
  4. evaluate_model      - Evaluate model metrics (never reached)
  5. publish_model       - Publish to model registry (never reached)
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import cast

from workchain import (
    CheckResult,
    PollPolicy,
    StepConfig,
    StepResult,
    async_step,
    completeness_check,
    step,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configs and Results
# ---------------------------------------------------------------------------


class DatasetConfig(StepConfig):
    dataset_name: str = "imagenet-mini"
    sample_size: int = 10_000


class DatasetResult(StepResult):
    dataset_id: str = ""
    record_count: int = 0


class SplitConfig(StepConfig):
    """No user-facing fields."""


class SplitResult(StepResult):
    train_count: int = 0
    test_count: int = 0


class TrainConfig(StepConfig):
    model_type: str = "resnet50"


class TrainResult(StepResult):
    job_id: str = ""


class EvalConfig(StepConfig):
    """Config for model evaluation (no user-facing fields — derived from training result)."""


class EvalResult(StepResult):
    accuracy: float = 0.0
    f1_score: float = 0.0


class PublishConfig(StepConfig):
    """Config for model publishing (no user-facing fields — derived from training result)."""


class PublishResult(StepResult):
    model_uri: str = ""
    version: str = ""


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------


@step(category="ML Training", description="Download and clean the training dataset")
async def prepare_dataset(
    config: DatasetConfig,
    _results: dict[str, StepResult],
) -> DatasetResult:
    """Download and clean the training dataset."""
    await asyncio.sleep(random.uniform(5, 20))
    dataset_id = f"ds-{uuid.uuid4().hex[:8]}"
    logger.info(
        "[dataset] Prepared %s: %d records (id=%s)",
        config.dataset_name, config.sample_size, dataset_id,
    )
    return DatasetResult(dataset_id=dataset_id, record_count=config.sample_size)


@step(category="ML Training", description="Partition dataset into train and test splits", depends_on=["prepare_dataset"])
async def split_train_test(
    _config: SplitConfig,
    results: dict[str, StepResult],
) -> SplitResult:
    """Split dataset into training and test partitions."""
    await asyncio.sleep(random.uniform(5, 20))
    train_ratio = 0.8
    ds = cast(DatasetResult, results["prepare_dataset"])
    train_count = int(ds.record_count * train_ratio)
    test_count = ds.record_count - train_count
    logger.info(
        "[split] Dataset %s: train=%d test=%d",
        ds.dataset_id, train_count, test_count,
    )
    return SplitResult(train_count=train_count, test_count=test_count)


@completeness_check()
async def check_training(
    _config: TrainConfig,
    _results: dict[str, StepResult],
    result: TrainResult,
) -> CheckResult:
    """Completeness check: training never converges — always returns incomplete.

    This simulates a GPU job that stalls (e.g. quota exhausted, gradient
    divergence) so the poll timeout fires.
    """
    await asyncio.sleep(random.uniform(3, 8))
    logger.info("[train] Job %s still running — loss not converging...", result.job_id)
    return CheckResult(complete=False, progress=0.1, message="Loss not converging")


@async_step(
    completeness_check=check_training,
    poll=PollPolicy(interval=3.0, backoff_multiplier=1.0, timeout=15.0, max_polls=20),
    category="ML Training",
    description="Submit model training job to compute cluster",
    depends_on=["split_train_test"],
)
async def train_model(
    config: TrainConfig,
    results: dict[str, StepResult],
) -> TrainResult:
    """Submit a model training job to the compute cluster."""
    await asyncio.sleep(random.uniform(5, 20))
    epochs = 100
    learning_rate = 0.001
    split = cast(SplitResult, results["split_train_test"])
    job_id = f"train-{uuid.uuid4().hex[:8]}"
    logger.info(
        "[train] Submitted %s training job %s (%d train samples, lr=%s)",
        config.model_type, job_id, split.train_count, learning_rate,
    )
    return TrainResult(job_id=job_id)


@step(category="ML Training", description="Evaluate model accuracy on test split", depends_on=["train_model"])
async def evaluate_model(
    _config: EvalConfig,
    results: dict[str, StepResult],
) -> EvalResult:
    """Evaluate model accuracy on test split."""
    await asyncio.sleep(random.uniform(5, 20))
    train = cast(TrainResult, results["train_model"])
    logger.info("[eval] Evaluating model from job %s", train.job_id)
    return EvalResult(accuracy=0.92, f1_score=0.89)


@step(category="ML Training", description="Publish trained model to the model registry", depends_on=["train_model"])
async def publish_model(
    _config: PublishConfig,
    results: dict[str, StepResult],
) -> PublishResult:
    """Publish trained model to the model registry."""
    await asyncio.sleep(random.uniform(5, 20))
    train = cast(TrainResult, results["train_model"])
    uri = f"registry/models/{train.job_id}"
    logger.info("[publish] Published model to %s", uri)
    return PublishResult(model_uri=uri, version="1.0.0")
