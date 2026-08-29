# ADR 012: Lineage Model — Fixed Foreign-Key Chain, Not a Generic Graph

## Status
Proposed -- pending user review before implementation.

## Context
"How was Model v17 produced" needs to be answerable as a query, tracing: dataset version -> training run (config, code commit, job/attempt) -> artifact -> model version -> (later) evaluations. There are two ways to represent this: a generic graph (nodes + typed edges, arbitrary relationships) or a fixed chain of foreign keys matching the known shape of the lineage.

## Decision: fixed FK chain
```
DatasetVersion  <---- training_runs.dataset_version_id
ModelVersion    <---- training_runs.base_model_version_id (nullable)
                        |
                        v
                  TrainingRun (also: job_id, training_config, code_commit,
                                container_image, random_seed)
                        |
                        v
                   Artifact (training_runs -> artifacts via the artifact
                              the run produced, once one exists)
                        |
                        v
                  ModelVersion.artifact_id, ModelVersion.training_run_id
                        |
                        v
                  Model.base_model_version_id (self-referential, for
                                                 the model's own version history)
```
"Full lineage for Model v17" is a straightforward join chain: `model_versions -> training_runs -> dataset_versions`, `model_versions -> artifacts`, `model_versions -> training_runs -> jobs -> attempts` (V0.3's existing tables), plus (once V0.7 exists) `model_versions -> evaluations`. No graph traversal algorithm needed -- it's a fixed set of joins because the *shape* of ML lineage is known and stable (this project isn't building a lineage system for arbitrary unknown pipeline shapes).

## Why a generic graph model is rejected as premature
A generic lineage graph (nodes of any type, edges of any relation, recursive traversal) is the right answer when the shape of relationships is genuinely unknown or needs to support arbitrary future pipeline topologies. V0.5's actual requirement is a *specific, known* chain -- dataset version feeds training run feeds artifact feeds model version feeds evaluation. Building a generic graph engine (edge tables, traversal queries, cycle handling) to represent a shape that's already fully known ahead of time is exactly the kind of speculative infrastructure this project has rejected at every prior version (V0.1 ADR 001's "no speculative schema fields," V0.4 ADR 008's rejection of weighted-fair-scheduling without a real tenant model to justify it). If a genuinely graph-shaped lineage need emerges later (e.g., a model produced by ensembling multiple base models, or a dataset assembled from multiple source datasets), that's a real, evidence-backed reason to revisit this decision -- not something to build speculatively now.

## Base-model lineage (the one place the chain isn't strictly linear)
`Model.base_model_version_id` (on `models`, nullable) and `training_runs.base_model_version_id` (nullable) both exist because a model can be a fine-tune of another model version -- this is still a fixed-shape relationship (one base model version per training run, if any), not a general graph; it's simply a second FK alongside `dataset_version_id`, not a new kind of edge-table concept.

## Alternatives considered
- **Generic `lineage_edges(from_id, from_type, to_id, to_type, relation)` table:** rejected per above -- solves a more general problem than V0.5 has, at the cost of every lineage query becoming a recursive/graph traversal instead of a fixed join, and losing referential-integrity guarantees a real FK gives you (a `lineage_edges` row can point at a deleted/nonexistent id with no DB-level prevention; a FK cannot).
- **Denormalized lineage snapshot stored directly on `ModelVersion` (a JSON blob of "here's everything about how this was made"):** rejected -- breaks the moment any upstream row's descriptive fields change (e.g., a `Dataset`'s name is edited -- ADR 010 allows this for the container, not the version) unless carefully re-synced, and duplicates data the FK chain already provides via a query. A snapshot-on-read (computed by the query, not stored) gets the same "here's the full lineage" answer without the sync-drift risk.

## Consequences
- Adding a genuinely new lineage relationship (e.g., V0.7's evaluations attaching to model versions) means adding one more FK-bearing table and one more join to the "full lineage" query -- not touching a generic edge-table schema. This is the intended tradeoff: fixed-shape lineage is easy to extend by adding a table, hard to extend by changing the *kind* of relationship (which V0.5 asserts won't be needed).
- Lineage queries are only as complete as the FK chain that exists at query time -- a `ModelVersion` created before `base_model_version_id` was populated (e.g., data migrated from an earlier version of this schema) would show an incomplete chain; this is an acceptable, documented limitation of a schema-evolution nature, not a design flaw.
