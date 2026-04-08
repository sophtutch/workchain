"""Step handlers, configs, and results for the Data Pipeline ETL workflow."""

from __future__ import annotations

import uuid
from typing import cast

from workchain import CheckResult, PollPolicy, StepConfig, StepResult, async_step, completeness_check, step

# Poll simulation state — keyed by load_id
_poll_counts: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------

class ExtractConfig(StepConfig):
    source_uri: str
    table_name: str
    batch_size: int = 1000


class SchemaConfig(StepConfig):
    expected_columns: list[str]
    strict: bool = True


class LoadConfig(StepConfig):
    warehouse_uri: str
    target_table: str


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

@step()
async def extract_from_source(
    config: ExtractConfig,
    _results: dict[str, StepResult],
) -> ExtractResult:
    """Simulate extracting records from a data source."""
    # In a real implementation this would query a database / API.
    record_count = config.batch_size * 3  # simulate 3 batches extracted
    return ExtractResult(
        records_extracted=record_count,
        source_uri=config.source_uri,
    )


@step()
async def validate_schema(
    config: SchemaConfig,
    results: dict[str, StepResult],
) -> SchemaResult:
    """Validate that extracted data matches the expected schema."""
    extract = cast(ExtractResult, results["extract_from_source"])

    # Simulate validation — in production, inspect actual column metadata.
    column_count = len(config.expected_columns)
    valid = column_count > 0 and extract.records_extracted > 0

    if config.strict and not valid:
        raise ValueError(
            f"Schema validation failed: expected {config.expected_columns}"
        )

    return SchemaResult(valid=valid, column_count=column_count)


@step()
async def transform_records(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> TransformResult:
    """Apply transformations: cleaning, mapping, deduplication."""
    extract = cast(ExtractResult, results["extract_from_source"])

    total = extract.records_extracted
    dropped = int(total * 0.02)  # simulate 2% dropped as invalid
    transformed = total - dropped

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
    load_id = result.load_id
    count = _poll_counts.get(load_id, 0) + 1
    _poll_counts[load_id] = count

    if count >= 3:
        transform = cast(TransformResult, results["transform_records"])
        return CheckResult(
            complete=True,
            progress=1.0,
            message=f"Loaded {transform.records_transformed} records",
        )

    progress = round(count / 3, 2)
    return CheckResult(
        complete=False,
        progress=progress,
        message=f"Loading batch {count}/3",
        retry_after=2.0,
    )


@async_step(
    completeness_check=check_load,
    poll=PollPolicy(interval=2.0, timeout=60.0, max_polls=10),
)
async def load_to_warehouse(
    _config: LoadConfig,
    _results: dict[str, StepResult],
) -> LoadResult:
    """Submit a batch load job to the data warehouse."""
    load_id = uuid.uuid4().hex[:12]
    return LoadResult(load_id=load_id, records_loaded=0)


# ---------------------------------------------------------------------------
# Final step: update catalog
# ---------------------------------------------------------------------------

@step()
async def update_catalog(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> CatalogResult:
    """Register the freshly loaded dataset in the data catalog."""
    load = cast(LoadResult, results["load_to_warehouse"])
    entry_id = f"catalog-{load.load_id}"
    return CatalogResult(catalog_entry_id=entry_id, updated=True)
