# Resource Model — V0.4

## Job resource request (kept deliberately simple)
```
resources:
  cpu: <int, cores>
  memory_mb: <int>
  gpu: <int, count>
```
No GPU model/type distinction (a "GPU" is fungible in V0.4 -- requesting 1 GPU is satisfied by any 1 available GPU slot). No fractional CPU, no memory in bytes/other units, no additional dimensions (disk, network bandwidth). If a job's `config` doesn't specify `resources`, a default is applied (documented in API_CHANGES_V0.4.md), not silently treated as zero-resource (a zero-resource job would trivially always admit, which hides real scheduling behavior).

## Cluster capacity (single aggregate pool per resource type)
```
capacity:
  total_cpu       total_memory_mb       total_gpu
  allocated_cpu   allocated_memory_mb   allocated_gpu
```
One row, cluster-wide. This is a deliberate simplification: V0.4 does not model *which* physical node has *which* resources (no per-node placement, no bin-packing). The scheduler answers "is there enough aggregate capacity" -- it does not answer "which specific machine should this run on." That question belongs to a real execution backend (Kubernetes/Ray/Slurm), which V0.4 explicitly does not build (REQUIREMENTS_V0.4.md non-goals) -- when a real backend is introduced later, per-node placement becomes its job, informed by (but not solved by) this aggregate accounting.

**Why aggregate, not per-node, for V0.4:** per-node tracking requires a node/worker registration concept (which workers exist, what capacity each reports, health-tracking those registrations) that doesn't exist anywhere in this project yet. Building it now, with no real multi-node deployment to validate it against, would be exactly the kind of speculative infrastructure this project has consistently rejected (V0.1 ADR 001, V0.2 ADR 003). Aggregate accounting is the simplest model that still answers V0.4's actual question ("can we admit this job") correctly.

## Reservation (the unit that ties a job's attempt to consumed capacity)
```
reservation:
  job_id, attempt_number   -- which attempt this capacity is held for
  cpu, memory_mb, gpu      -- amounts held (copied from the job's request at admission time)
  created_at
  released_at              -- NULL while held; set exactly once on release
```
A reservation is scoped to `(job_id, attempt_number)`, mirroring V0.3's `attempts` table -- a retry gets a fresh reservation tied to its own attempt number, not a reused one. This keeps the audit trail (RESOURCE_MODEL answering "who held what, when") as granular as the execution history it corresponds to.

## Priority
```
priority: int, 0-100, default 50
```
Not tiered into named bands (no enum like "production"/"experimentation") -- a plain integer, with the *meaning* of specific bands left to whoever creates jobs (documented convention in API_CHANGES_V0.4.md, not enforced by the schema). Enforcing named tiers would be premature categorization without real usage data on what tiers actually matter.

## What this model explicitly excludes (and why)
- **GPU memory / GPU type:** every GPU is fungible in V0.4. Modeling heterogeneous GPU types (A100 vs H100, different VRAM) is real complexity worth having once real training jobs (V0.6+) actually need to express it -- inventing it now against a simulated executor would be guessing at a shape with no real requirement driving it.
- **Node-level placement:** see above.
- **Quotas/reservations per team or user:** no multi-tenancy concept exists yet (same reasoning as ADR 008's rejection of weighted-fair-scheduling for V0.4).
- **Preemption:** a job holding a reservation and running is never interrupted to free capacity for a higher-priority arrival -- consistent with this project's "cooperative, not preemptive" stance since V0.2/V0.3's cancellation design.
