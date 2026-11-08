# knowledgebase

A store of small, individually-addressable facts an AI agent reads and grows over
time. Each fact is one file with front-matter (a short name, a one-line
description for relevance, a type) and links to related items; an index lists them
for recall.

- **Recall** by relevance; bounded reads (never dump the whole store).
- **Grow-via-review**: new/changed facts land in an inbox as proposals; a review
  promotes them onto the shelf (where the auto-evolution loop deposits what a run
  learned). Duplicates merged, wrong items deleted.
- **Freshness**: last-used / last-reviewed tracking and a staleness report.
- **Consistency check**: the index matches the files; front-matter is well-formed.

Built on `agent-policy-kit` (audit, secret masking, path containment). See
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) Part B.
