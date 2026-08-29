# ADR 011: Artifact Storage — Content-Addressed Bytes in S3-Compatible Storage

## Status
Proposed -- pending user review before implementation.

## Context
Dataset files and model weights are large binaries that don't belong in PostgreSQL (bloats the database, breaks backup/replication assumptions, wastes a transactional store on something that doesn't need transactions). They need a separate store, and that store's failure modes are fundamentally different from Postgres's -- there is no shared transaction between "write a row" and "write an object," which is precisely the hard problem this version exists to solve (ADR 013).

## Decision: MinIO (S3-compatible) for bytes, content-addressed identity
- **Storage:** MinIO locally (S3 API-compatible, same posture as V0.2's Redpanda-for-Kafka choice -- swappable for real AWS S3 later without application code changes, since the client only ever speaks the S3 API).
- **Identity:** every artifact's storage key is derived from its content hash (`sha256`), e.g. `artifacts/sha256/<hash>`. Not a random UUID, not a sequential id.

**Why content-addressed identity, specifically:** it makes every consistency problem in ADR 013 dramatically simpler, for one reason -- **uploading the same content twice is naturally idempotent**. Two processes (or the same process retried after a crash) uploading identical bytes compute the identical hash, write to the identical key, and the second write is either a no-op (object already exists) or an overwrite with byte-identical content -- never a duplicate, never a conflict. This directly answers requirement "duplicate artifact upload" without any additional deduplication logic: the storage layout *is* the deduplication.

It also makes reconciliation (ADR 013) tractable: given a `PENDING` metadata row with a known content hash, reconciliation can check "does an object exist at this exact key" as the single source of truth for "did the bytes actually make it," with no ambiguity about which of several possible uploads it's checking.

## Decision: PostgreSQL owns metadata, object storage owns bytes -- stated precisely
```
PostgreSQL:
  artifacts (id, content_hash, storage_key, artifact_type, size_bytes,
             status, created_at, uploaded_at)
  -- the row's EXISTENCE and STATUS are the only things Postgres claims
  -- authority over. It does not claim to know the bytes are good beyond
  -- what its own recorded content_hash implies.

Object storage:
  the actual bytes at storage_key
  -- has no concept of "status," "training run," "lineage" -- it is a
  -- dumb, content-addressed blob store, nothing more.
```
A `Dataset`/`Model`/checkpoint reference in any other table points at an `artifacts.id`, never directly at a storage key -- this keeps the storage-layer detail (bucket names, key layout, which S3-compatible provider) entirely behind the `artifacts` table, swappable without touching `datasets`, `dataset_versions`, `models`, `model_versions`, or `training_runs`.

## One `artifacts` table for all binary kinds (dataset files, model weights, checkpoints)
`artifact_type` discriminates (`DATASET`, `MODEL`, `CHECKPOINT`) rather than three separate tables with duplicated upload/consistency logic. The consistency problem (ADR 013) is identical regardless of *what* the bytes represent -- solving it once, generically, is simpler than solving it three times with subtly different edge cases each time. This mirrors V0.3's `attempts` table being generic across job types rather than per-job-type execution-history tables.

## Alternatives considered
- **Store artifacts directly in PostgreSQL (bytea/large objects):** rejected -- explicitly ruled out by REQUIREMENTS_V0.5.md and standard practice; bloats backups, breaks the "Postgres is a fast transactional metadata store" assumption every prior version relied on.
- **UUID-based artifact identity instead of content hash:** rejected -- loses the free deduplication and reconciliation-simplicity content-addressing provides; would require separately implemented dedup logic achieving strictly worse guarantees.
- **A real cloud object store (AWS S3) from day one:** rejected for local dev, same reasoning as Redpanda-for-Kafka -- MinIO speaks the identical API, zero cost to swap later, zero cloud dependency now (consistent with the project's "keep the stack local/free" original constraint).

## Consequences
- Artifact upload must compute the hash before (or while) uploading -- for large files this means streaming-hash-then-upload or hash-as-you-stream, not "read the whole file into memory first." Not a correctness concern for V0.5 (still simulated/small artifacts), but a real constraint documented now so V0.6+ (real model checkpoints, potentially gigabytes) doesn't have to relearn it.
- Every artifact reference elsewhere in the schema is a foreign key to `artifacts.id`, never a raw storage key -- this is what keeps ADR 013's reconciliation logic centralized in one place.
