# ADR 010: Dataset Versioning — Immutable, Content-Identified Versions

## Status
Proposed -- pending user review before implementation.

## Context
Datasets change over time (new data added, corrections made), but once a `TrainingRun` has used a specific version of a dataset, that version must never change under it -- otherwise "exactly how was Model v17 produced" becomes unanswerable (the dataset it referenced yesterday isn't the dataset it references today). Need a versioning scheme that makes "immutable" a structural guarantee, not a documented convention someone can accidentally violate.

## Decision: Dataset (mutable container) + DatasetVersion (immutable, content-hashed)
```
Dataset                          DatasetVersion
  id                               id
  name                             dataset_id (FK)
  description                      version_number (1, 2, 3...)
  created_at                       artifact_id (FK -> artifacts, ADR 011)
  (mutable: name/description        created_at
   can be edited -- these are        (IMMUTABLE once created: no
   organizational metadata,           update path exists for this
   not content)                      row at all except the artifact's
                                      own PENDING->UPLOADED transition,
                                      ADR 013)
```
A `Dataset` is a named container (like a Git repository); a `DatasetVersion` is one immutable snapshot (like a specific commit). Uploading new content never edits an existing `DatasetVersion` row -- it always creates a new one with the next `version_number`, referencing a new `artifacts` row (new content hash, since the bytes differ).

**Why immutability is structural, not just documented:** there is no application code path that updates a `DatasetVersion`'s `artifact_id` or content-relevant fields after creation -- the only field that ever changes post-creation is inherited from the artifact's own lifecycle (`PENDING -> UPLOADED`/`FAILED`, ADR 013), which is about upload completion, not content mutation. `name`/`description` on the *Dataset* (not the version) can be edited because they're organizational labels, not content -- exactly the same distinction V0.1 drew between `jobs.config` (immutable-in-practice payload) and job status metadata.

## Decision: version identity is sequential per-dataset, content identity is the hash
`version_number` is a simple per-`Dataset` sequence (1, 2, 3...) for human-readable reference ("dataset v42" reads naturally); the actual content identity backing it is the artifact's content hash (ADR 011). Two datasets' versions never collide in meaning even if their `version_number`s happen to match (they're scoped by `dataset_id`), and the same underlying bytes uploaded to two different datasets would (correctly) get two different `DatasetVersion` rows with two different `version_number`s, sharing one `artifacts` row (content-addressing naturally allows this -- one artifact, multiple version records pointing at it, no duplicate storage).

## Duplicate dataset version registration
Uploading byte-identical content as a "new version" of the same dataset: the artifact layer (ADR 011) already dedupes the bytes (same hash, same storage key, no duplicate storage), but a **new `DatasetVersion` row is still created** with the next `version_number` -- because the *event* of "this content was registered as a version at this time" is meaningful lineage information even if the bytes are identical to a previous version (e.g., someone explicitly re-confirming a dataset snapshot). This is a deliberate choice: don't conflate "same bytes" with "same version" at the registration-event level, only at the storage level.

## Alternatives considered
- **Mutable dataset rows with a separate audit log:** rejected -- this is strictly weaker than structural immutability; an audit log can be incomplete or bypassed, whereas "no UPDATE code path exists" cannot.
- **Git-like content-addressed dataset identity (version_number = hash, no separate Dataset container):** rejected -- loses the human-readable "dataset X, version N" framing this project's target lineage output (`dataset: customer_data v42`) explicitly asks for; the sequential `version_number` inside a named `Dataset` container is what makes that readable.
- **Deduping at the DatasetVersion level (reject re-registering identical content):** rejected -- see "duplicate dataset version registration" above; the registration event itself is meaningful, only the byte storage should dedupe.

## Consequences
- `DatasetVersion` rows accumulate forever (no deletion path) -- acceptable at V0.5's scale; a real retention/archival policy is future scope if this becomes an actual operational concern.
- Any `TrainingRun` referencing a `DatasetVersion` gets a permanently stable reference -- the whole point of this ADR.
