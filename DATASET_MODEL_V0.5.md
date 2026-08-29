# Dataset Model — V0.5

Full rationale in ADR 010. Operational specification here.

## Concepts
- **Dataset** -- named container, mutable name/description, immutable identity (id).
- **DatasetVersion** -- immutable snapshot, references one `artifacts` row.

## Creating a dataset version
```
POST /v1/datasets/{id}/versions  (multipart upload or pre-signed-URL flow -- implementation detail, not fixed by this design doc)
  |
  v
1. artifact created (PENDING, ADR 013), upload begins
2. once artifact reaches UPLOADED (synchronously in the request, for V0.5's
   expected small/simulated dataset sizes -- not an async job), insert
   dataset_versions row: dataset_id, version_number = max(existing)+1,
   artifact_id
```
If the artifact never reaches `UPLOADED` (upload fails), no `DatasetVersion` row is created at all -- the caller gets an error, and the orphaned `PENDING`/eventually-`FAILED` artifact is handled by the Reconciler like any other failed upload. This avoids ever having a `DatasetVersion` row pointing at a non-`UPLOADED` artifact (the hard invariant from ARTIFACT_LIFECYCLE_V0.5.md).

## Reading dataset versions
`GET /v1/datasets/{id}/versions` lists all versions, each showing `version_number`, `content_hash`, `size_bytes`, `created_at`. `GET /v1/datasets/{id}/versions/{version_number}` returns one, including enough to retrieve the actual bytes (a storage reference, not the bytes themselves inline -- consistent with "never put artifact bytes in a JSON API response").

## Two training runs referencing the same dataset version concurrently
No special handling needed: `DatasetVersion` is immutable and read-only once `UPLOADED` (ADR 010) -- any number of concurrent `TrainingRun`s can reference the same `dataset_version_id` with no coordination, no locking, because nothing about the dataset version can change underneath them. This is the entire point of immutability, stated as an explicit non-problem rather than something requiring its own concurrency mechanism.

## Dataset version modified after a training run starts
Cannot happen -- `DatasetVersion` rows have no update path (ADR 010). "Modifying a dataset" always means creating a *new* `DatasetVersion`; any `TrainingRun` that started against version 42 continues to reference version 42 forever, regardless of whether version 43 is later created. This is the direct payoff of ADR 010's immutability decision.
