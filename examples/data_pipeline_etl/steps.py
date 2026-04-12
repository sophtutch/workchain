"""Step handlers, configs, and results for the Data Pipeline ETL workflow."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import cast

from workchain import CheckResult, PollPolicy, StepConfig, StepResult, async_step, completeness_check, step

logger = logging.getLogger(__name__)

# Poll simulation state — keyed by load_id
_poll_counts: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------

class ExtractConfig(StepConfig):
    source_uri: str
    table_name: str


class SchemaConfig(StepConfig):
    """No user-facing fields — schema expectations are defined in the handler."""


class TransformConfig(StepConfig):
    """Config for record transformation (no user-facing fields — derived from extraction)."""


class LoadConfig(StepConfig):
    target_table: str


class CatalogConfig(StepConfig):
    """Config for catalog update (no user-facing fields — derived from load result)."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

class ExtractResult(StepResult):
    records_extracted: int = 0
    source_uri: str = ""


class SchemaResult(StepResult):
    valid: bool = False
    column_count: int = 0


class TransformResult(StepResult):
    records_transformed: int = 0
    dropped: int = 0


class LoadResult(StepResult):
    load_id: str = ""
    records_loaded: int = 0


class CatalogResult(StepResult):
    catalog_entry_id: str = ""
    updated: bool = False


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------

@step(category="ETL Pipeline", description="Extract records from a data source")
async def extract_from_source(
    config: ExtractConfig,
    _results: dict[str, StepResult],
) -> ExtractResult:
    """Simulate extracting records from a data source."""
    await asyncio.sleep(random.uniform(5, 20))
    batch_size = 1000
    # In a real implementation this would query a database / API.
    record_count = batch_size * 3  # simulate 3 batches extracted
    logger.info(
        "[extract] Extracted %d records from %s (table=%s, batch_size=%d)",
        record_count, config.source_uri, config.table_name, batch_size,
    )
    return ExtractResult(
        records_extracted=record_count,
        source_uri=config.source_uri,
    )


@step(category="ETL Pipeline", description="Validate extracted data matches expected schema", depends_on=["extract_from_source"])
async def validate_schema(
    _config: SchemaConfig,
    results: dict[str, StepResult],
) -> SchemaResult:
    """Validate that extracted data matches the expected schema."""
    await asyncio.sleep(random.uniform(5, 20))
    expected_columns = ["id", "timestamp", "event_type", "payload"]
    extract = cast(ExtractResult, results["extract_from_source"])

    column_count = len(expected_columns)
    valid = column_count > 0 and extract.records_extracted > 0

    if not valid:
        raise ValueError(
            f"Schema validation failed: expected {expected_columns}"
        )

    logger.info(
        "[schema] Validated %d columns against %d extracted records from %s",
        column_count, extract.records_extracted, extract.source_uri,
    )
    return SchemaResult(valid=valid, column_count=column_count)


@step(category="ETL Pipeline", description="Clean, map and deduplicate records", depends_on=["extract_from_source"])
async def transform_records(
    _config: TransformConfig,
    results: dict[str, StepResult],
) -> TransformResult:
    """Apply transformations: cleaning, mapping, deduplication."""
    await asyncio.sleep(random.uniform(5, 20))
    extract = cast(ExtractResult, results["extract_from_source"])

    total = extract.records_extracted
    dropped = int(total * 0.02)  # simulate 2% dropped as invalid
    transformed = total - dropped

    logger.info(
        "[transform] Transformed %d records (%d dropped as invalid) from %s",
        transformed, dropped, extract.source_uri,
    )
    return TransformResult(records_transformed=transformed, dropped=dropped)


# ---------------------------------------------------------------------------
# Async step: load_to_warehouse (with polling)
# ---------------------------------------------------------------------------

@completeness_check()
async def check_load(
    _config: LoadConfig,
    results: dict[str, StepResult],
    result: LoadResult,
) -> CheckResult:
    """Completeness check for the warehouse load.

    Simulates a batch load that completes after 3 polls, reporting
    incremental progress each time.
    """
    await asyncio.sleep(random.uniform(3, 8))
    load_id = result.load_id
    count = _poll_counts.get(load_id, 0) + 1
    _poll_counts[load_id] = count

    if count >= 3:
        transform = cast(TransformResult, results["transform_records"])
        logger.info(
            "[load] Load %s complete: %d records loaded in %d polls",
            load_id, transform.records_transformed, count,
        )
        return CheckResult(
            complete=True,
            progress=1.0,
            message=f"Loaded {transform.records_transformed} records",
        )

    progress = round(count / 3, 2)
    logger.info("[load] Load %s poll %d/3 (progress=%.0f%%)", load_id, count, progress * 100)
    return CheckResult(
        complete=False,
        progress=progress,
        message=f"Loading batch {count}/3",
        retry_after=2.0,
    )


@async_step(
    completeness_check=check_load,
    poll=PollPolicy(interval=2.0, timeout=60.0, max_polls=10),
    category="ETL Pipeline",
    description="Submit batch load job to the data warehouse",
)
async def load_to_warehouse(
    _config: LoadConfig,
    _results: dict[str, StepResult],
) -> LoadResult:
    """Submit a batch load job to the data warehouse."""
    await asyncio.sleep(random.uniform(5, 20))
    load_id = uuid.uuid4().hex[:12]
    logger.info("[load] Submitted batch load job %s to data warehouse", load_id)
    return LoadResult(load_id=load_id, records_loaded=0)


# ---------------------------------------------------------------------------
# Final step: update catalog
# ---------------------------------------------------------------------------

@step(category="ETL Pipeline", description="Register loaded dataset in the data catalog", depends_on=["load_to_warehouse"])
async def update_catalog(
    _config: CatalogConfig,
    results: dict[str, StepResult],
) -> CatalogResult:
    """Register the freshly loaded dataset in the data catalog."""
    await asyncio.sleep(random.uniform(5, 20))
    load = cast(LoadResult, results["load_to_warehouse"])
    entry_id = f"catalog-{load.load_id}"
    logger.info("[catalog] Registered dataset %s in data catalog as %s", load.load_id, entry_id)
    return CatalogResult(catalog_entry_id=entry_id, updated=True)
