# Two-session agent memory evaluation

A returning application or coding agent reads a 2,048-byte evidence bundle and selects its first cited fact, or its first passage when the store returns only passages. It abstains on an empty bundle. A task passes when **both the selected value or passage and its original source ID** match the expected action. All 24 scenarios use synthetic records. No external language model generates or grades the answers. [Task fixture](../evals/agent-cases.json) · [All measured responses](../evals/agent-results.json)

| Metric | Gravity 0.3 | 0.4 first pass | 0.4 after ranking change |
|---|---:|---:|---:|
| Application tasks | 8/12 | 12/12 | 12/12 |
| Coding tasks | 6/12 | 11/12 | 12/12 |
| Correct abstentions | 2/11 | 11/11 | 11/11 |
| Bundles containing a specified obsolete statement | 12/13 | 0/13 | 0/13 |
| Median / p95 read latency in ms | 3.08 / 5.60 | 9.65 / 13.85 | 8.13 / 12.26 |
| Median / largest serialized response in bytes | 459.5 / 959 | 367.5 / 649 | 367.5 / 649 |

The fixture exercises superseded and backdated decisions, assistant-authored suggestions, expiry, disagreement, retracted lessons, subject-specific preferences, archival instructions, unknowns, and cross-document questions. The severity of a stale hit depends on the consuming agent. Counting every obsolete string measures what that agent can see even when its first choice is correct.

## What failed and what changed

On the first pass a coding agent asked which command builds the service with the active runtime. The first match named the runtime, Bun. The cited command, `bun run compile`, ranked second. Three separately phrased one-result probes also selected the qualifying record over the requested mailbox, timeout, or export format. All three failed before the ranking change and passed afterward. The reranker recognizes the requested field in an explicit *what* or *which* question and uses it to order eligible current facts. It preserves lexical order for ties. The final 24/24 is a post-tuning acceptance result. Deeper reasoning that requires deriving a new answer from several passages remains unmeasured. [Ranking implementation](../gravity_memory/agent_recall.py) · [Independent phrasing regression tests](../tests/test_agent_runs.py)

Gravity 0.3 already returned current facts accurately on many of these tasks. Its combined context also supplied historical passages. If a memory was revoked, disputed, or assistant-authored, a first-passage fallback could act on it. The 0.4 agent path withholds those passages and retains the reasons in the run trace. Reading and persisting that trace incurred additional latency in this comparison.

## Method and reproduction

Run `python -m evals.agent_runs` from a checkout containing `c317fb4`. The runner extracts that commit to a temporary directory and loads each implementation in its own Python process. Both get the same writes, timestamps, query, eight-result limit, byte ceiling, full-text route, and deterministic reader. Each task uses a fresh SQLite file. A second Ledger instance represents the returning client. In 0.4 the first client hands the run to the second. For 0.3 an adapter retains the original fact and passage order and packs its native response into the same citation fields. The learning scenarios use the available manual annotation and retirement operations on 0.3. The middle column runs the current code with the requested-field ranking disabled.

Measurements include the local recall call, envelope construction and, on 0.4, synchronous trace persistence. They exclude setup, transport, external model calls, and answer generation. Latency is a single Windows run across 24 small namespaces. The p95 is the nearest-rank statistic. Bytes are the complete compact UTF-8 memory response, excluding the HTTP, SDK, or MCP framing. A dedicated deployment with many concurrent agents and larger namespaces needs its own latency measurement. Equivalent live Mem0 and Supermemory deployments were unavailable for this comparison.

Run `python -m evals.run --version 0.4.0 --out evals/team-regression.json` to reproduce the original natural-language correction suite. The default retriever returned the correct first fact in 14/18 answerable queries, exposed obsolete facts on 0/11 probes, and returned empty on 9/9 unknowns. [Original workload results](../evals/team-regression.json)
