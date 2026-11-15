# Frozen AKT five-level probe benchmark

From the PyKT repository, with both repositories on PYTHONPATH:

```bash
PYTHONPATH=.:../pianokt/backend MPLCONFIGDIR=/tmp/kt-mpl \
 /Users/tse/miniconda3/envs/pykt/bin/python -m pianokt_backend.experiments.cli \
 --mode mock --output /tmp/kt-benchmark --selection-mc 4 --evaluation-mc 8 --reference-mc 16
```

Default production-sized budgets are 32/256/512; budget sweep defaults to
4,8,16,32,64,128 with three independent repeated rankings. These may be expensive.
No HTTP API or persistent student store is used. All updates are immutable local caches.

Offline config YAML accepts `checkpoint`, `snapshot`, `mc_budgets`, `budget_repeats`.
The snapshot JSON must include `verified: true`, `source_environment: development|test`,
`checkpoint_sha256`, `kc_labeler_version: "1.2.0"`, `candidates`, and `students`.
This is an administrator-exported trust contract, not a substitute for source verification.
Candidates contain stable `song_id`, `segment_id`, `segment_index`, `song_version`,
`midi_path`, `duration_seconds`, and `provenance` (context=`standalone_reference`,
MIDI SHA256, labeler version, nonempty evidence). KC is re-extracted with the existing
ReferenceCandidates implementation, not accepted as arbitrary encoded IDs.
Students contain student_id, checkpoint_id, and chronologically ordered history entries
with kc_vector and response. Export only verified READY records at or before the cutoff.
Do not put credentials in config or snapshot. Direct dev database export is not implemented.

Outputs contain anonymous hashes instead of student IDs. One record per checkpoint permits
resuming completed checkpoints; manifest pins input and implementation digests. Interrupted
checkpoints are recomputed. Raw prediction arrays are in per-checkpoint JSON.
The five-level bank covers all known distinct candidate KC before candidate limiting.
Unknown candidates are excluded uniformly for every method. Empty banks fail closed.

Reference regret is conditional on the configured candidate subset and a noisy reference.
No adaptive near-tie reference refinement is implemented. No claim of exact oracle optimality.
Cluster bootstrap averages within student before averaging across students; a single student
has no population confidence interval. Mock results only verify software mechanics.
Reported runtime is independent evaluation time, not end-to-end method selection cost.

Still incomplete relative to the full research protocol: production-grade verified history
export and ordering validation, direct CatalogCandidateProvider adapter, adaptive reference,
history-length/model/probe sensitivity, all ten final figures, complete operation accounting,
and held-out real future outcomes. Do not call this a completed scientific benchmark.
