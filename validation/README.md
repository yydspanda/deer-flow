# Validation

This directory contains offline validation tools, not production Runtime code.

## Layout

- `compact_zeus/`: PingAn/Zeus corpus construction, adapter field audits, review artifacts,
  and encoded-context validation.
- `compact_zeus/internal_batch/`: restricted-PKL Runtime batches plus the PI-01E paired
  Runtime/investigation shadow evaluator; generated artifacts remain local and Git-ignored.
- `compact_zeus/docs/`: durable design and review documents.
- `compact_zeus/data/`: sensitive, gitignored, reproducible outputs grouped into `corpus/`,
  `audits/`, `reviews/`, `compaction/`, and `exploration/`.

The corresponding local inputs are under `datas/source/` and `datas/legacy_demos/`.
Start with [`compact_zeus/README.md`](compact_zeus/README.md); do not import validation
modules from production code.
