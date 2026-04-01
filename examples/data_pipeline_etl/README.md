# Data Pipeline ETL Example

A five-step ETL workflow built with **workchain** that demonstrates extraction,
schema validation, transformation, async bulk loading with polling, and catalog
registration.

## Workflow steps

```mermaid
flowchart LR
    A[extract_from_source] --> B[validate_schema]
    B --> C[transform_records]
    C --> D[load_to_warehouse]
    D --> E[update_catalog]

    style D fill:#f9e79f,stroke:#f4d03f,color:#000
```

| Step | Type | Description |
|------|------|-------------|
| `extract_from_source` | sync | Pull records from the source database |
| `validate_schema` | sync | Verify columns match the expected schema |
| `transform_records` | sync | Clean, map, and deduplicate records |
| `load_to_warehouse` | **async** | Submit a batch load and poll until complete |
| `update_catalog` | sync | Register the dataset in the data catalog |

## Typed configs and results

Each step uses typed `StepConfig` / `StepResult` subclasses so downstream steps
can safely cast results with `typing.cast()`.

- **ExtractConfig** -- `source_uri`, `table_name`, `batch_size`
- **SchemaConfig** -- `expected_columns`, `strict`
- **LoadConfig** -- `warehouse_uri`, `target_table`

## Running the demo

```bash
pip install mongomock-motor   # in-memory Mongo for local testing
python -m examples.data_pipeline_etl.example
```

## Key patterns demonstrated

- **Async polling** -- `load_to_warehouse` submits a job, then the engine polls
  `check_load` every 2 seconds. The check simulates progress (33% / 67% / 100%)
  and completes after three polls.
- **Cross-step result access** -- `validate_schema` reads from `ExtractResult`;
  `update_catalog` reads from `LoadResult`, both via `cast()`.
- **Schema validation gate** -- In strict mode, `validate_schema` raises on
  invalid schemas, causing the workflow to fail fast.
