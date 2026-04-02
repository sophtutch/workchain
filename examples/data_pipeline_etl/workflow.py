"""Workflow builder for the Data Pipeline ETL example."""

from __future__ import annotations

from examples.data_pipeline_etl.steps import ExtractConfig, LoadConfig, SchemaConfig
from workchain import PollPolicy, Step, Workflow


def build_workflow(
    source_uri: str,
    target_table: str,
    columns: list[str],
    *,
    warehouse_uri: str = "warehouse://localhost:5439/analytics",
    batch_size: int = 1000,
) -> Workflow:
    """
    Build a five-step ETL workflow.

    Args:
        source_uri: Connection string for the data source.
        target_table: Destination table in the warehouse.
        columns: Expected column names for schema validation.
        warehouse_uri: Connection string for the warehouse.
        batch_size: Number of records per extraction batch.
    """
    return Workflow(
        name="data_pipeline_etl",
        steps=[
            Step(
                name="extract_from_source",
                handler="examples.data_pipeline_etl.steps.extract_from_source",
                config=ExtractConfig(
                    source_uri=source_uri,
                    table_name=target_table,
                    batch_size=batch_size,
                ),
            ),
            Step(
                name="validate_schema",
                handler="examples.data_pipeline_etl.steps.validate_schema",
                config=SchemaConfig(
                    expected_columns=columns,
                    strict=True,
                ),
            ),
            Step(
                name="transform_records",
                handler="examples.data_pipeline_etl.steps.transform_records",
            ),
            Step(
                name="load_to_warehouse",
                handler="examples.data_pipeline_etl.steps.load_to_warehouse",
                is_async=True,
                completeness_check="examples.data_pipeline_etl.steps.check_load",
                poll_policy=PollPolicy(
                    interval=2.0,
                    timeout=60.0,
                    max_polls=10,
                ),
                config=LoadConfig(
                    warehouse_uri=warehouse_uri,
                    target_table=target_table,
                ),
            ),
            Step(
                name="update_catalog",
                handler="examples.data_pipeline_etl.steps.update_catalog",
            ),
        ],
    )
