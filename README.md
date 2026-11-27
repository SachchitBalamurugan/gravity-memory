# Recall Ledger

A local memory service for agents that need to answer **what is current, when did it change, and who said so?** It stores an original document, cited facts and search indexes in one SQLite transaction. An accepted write is readable immediately. Python 3.11+ with SQLite FTS5 runs the core offline. Optional local models can suggest facts or retrieve semantically related passages.

[Customer research and competitor code analysis](RESEARCH.md) · [Raw retrieval results](evals/local-results.json) · [Storage measurements](evals/scale-results.json)

## Try a correction

Run from the project directory. The demo uses a temporary database and cleans up afterwards.

```powershell
python -m recall_ledger demo
```

The demo writes a move from Seattle to Tucson, then imports an older Reno conversation. The current lookup returns Tucson, January's lookup returns Seattle, and a repeated write returns its original receipt. The standalone distribution also runs with `python dist/recall-ledger.pyz demo` and can be copied to another machine with Python and SQLite FTS5. From a source checkout, rebuild the portable application and source ZIP with `python scripts/package.py`.

For a persistent workspace, give each customer or project its own database. From a trusted app, select the database using the authenticated user's identity. Two agent processes launched with the same file can share that history.

```powershell
python -m recall_ledger --db .ledger/demo.sqlite add --id turn-1 --source chat/1 --role user --text "I live in Seattle." --at 2026-01-01T00:00:00Z
python -m recall_ledger --db .ledger/demo.sqlite add --id turn-2 --source chat/2 --role user --text "I moved to Tucson." --at 2026-02-01T00:00:00Z
python -m recall_ledger --db .ledger/demo.sqlite lookup home.city
python -m recall_ledger --db .ledger/demo.sqlite lookup home.city --at 2026-01-15T00:00:00Z
python -m recall_ledger --db .ledger/demo.sqlite history home.city
python -m recall_ledger --db .ledger/demo.sqlite search "Seattle" --view archive
```

## Application writes

```python
from recall_ledger import Ledger

memory = Ledger(".ledger/account-821.sqlite")
receipt = memory.remember(
    request_id="ticket-104/change-2",
    source="support/ticket-104",
    text="For after-hours incidents please page Kim.",
    effective_at="2026-04-01T09:00:00-07:00",
    facts=[{
        "key": "support.after_hours.primary",
        "value": "Kim",
        "quote": "after-hours incidents please page Kim",
    }],
)
answer = memory.lookup("support.after_hours.primary")
proof = answer["evidence"][0]
original = memory.source(proof["request_id"])["text"]
assert original[proof["start"]:proof["stop"]] == proof["quote"]
```

Use a stable request ID from the calling system. An identical retry returns the previous acknowledgment. Different input under an existing ID raises `Conflict` and maps to HTTP 409. A second independent source with identical text can still contribute evidence. Supplied quotes and values must occur verbatim in the original. The caller can specify a zero-based Unicode code-point offset with the fact's `start` field to disambiguate repeated passages.

Choose single-valued field names. Two concurrent roles can use distinct keys such as `support.primary` and `support.backup`. The application owns the subject and field vocabulary. Two names for the same field create two timelines. Exact aliases, entity resolution across arbitrary documents, and the truth of a quoted statement need application or reviewer judgment.

| Read or write | Behavior |
|---|---|
| `remember(..., role="document")` | Archives the full text with overlapping, indexed passages. Supplied facts become part of the profile. Default role for imports and attachments. |
| `remember(..., role="user")` | With facts omitted, a small English parser recognizes declarations about name, home city, employer, timezone and contact channel. An explicit list selects the caller's facts. An empty list archives without inference. |
| `remember(..., role="assistant")` | Archives assistant output. The assistant's own guesses have no automatic route into the profile from that turn. Source roles are assertions by the trusted caller. |
| `lookup(key, as_of=..., known_at=...)` | Picks the latest effective revision known by the requested recording time. Returns current, unknown, expired, revoked or disputed, plus evidence where available. A newer expired or revoked revision suppresses older values. Differing assertions for the same effective instant produce a dispute. |
| `lookup(..., require_review=True)` | May return pending_review and the IDs of at most three newer eligible unclassified user turns. The check is workspace-wide and can hold unrelated facts. Partially parsed turns and off-topic matches still require application judgment. |
| `search(query)` | Ranks lexical matches over current fact keys and values. Returns the matched source, BM25 rank and query terms. Free-text questions can retrieve the wrong subject. Use a canonical key for a field the application knows. |
| `search(query, view="archive")` | Returns matching original passages with source offsets. Superseded and expired claims can be present in historical material. |
| Revocation | Write a new cited fact with the same key and `value: null`. Its history remains auditable. A fact can also carry an `expires_at` timestamp. Expiry is exclusive. |
| `erase()` | Removes originals, annotations, versions, receipts, index entries and vectors for this database from application access. Exports, backups and physical media have their own retention lifecycle. |

## Review an extraction miss

An original can be searchable before an operator knows which facts to promote. This example handles a sentence outside the default grammar.

```powershell
python -m recall_ledger --db .ledger/demo.sqlite add --id turn-3 --source chat/3 --role user --text "These days I call Ghent home."
python -m recall_ledger --db .ledger/demo.sqlite pending
python -m recall_ledger --db .ledger/demo.sqlite lookup home.city --require-review
python -m recall_ledger --db .ledger/demo.sqlite annotate turn-3 --id review-3 --key home.city --value Ghent --quote "I call Ghent home"
python -m recall_ledger --db .ledger/demo.sqlite lookup home.city
```

The annotation references turn-3, adopts its effective time and records when the review happened. An earlier knowledge-time query still reconstructs what the service could return before the review. Use `annotate turn-3 --id review-3 --dismiss` for a previously unreviewed turn with no relevant claims. For several facts in one review, use `call annotate` with a JSON array under `facts`. To resolve an actual same-time disagreement, submit a new authoritative source with a later effective time. The pending list covers originals that produced zero facts. A partly extracted multi-fact source can be reviewed directly by ID.

For optional model-assisted review, create `fields.json` with definitions such as `{"home.city":"Current primary home city of the account holder"}`. With a locally installed Ollama chat model, run `python -m recall_ledger --db .ledger/demo.sqlite suggest turn-3 --fields fields.json --chat-model YOUR_MODEL`. The output is a proposal. The program checks allowed fields and source spans, and an operator or trusted application issues the annotation. Each invocation sends one source to the loopback Ollama service. [Ollama structured output API](https://docs.ollama.com/capabilities/structured-outputs)

## Use from other tools

**MCP over stdio.** Point an MCP client at Python and the portable application. This example uses absolute Windows paths. Change the database path to bind the tool server to the intended workspace. It advertises remember, lookup, search, history, source, pending, annotate and status. Use separate server configurations for separate customers.

```json
{
  "mcpServers": {
    "recall-ledger": {
      "command": "python",
      "args": ["C:/Users/sachc/Projects/recall-ledger/dist/recall-ledger.pyz",
               "--db", "C:/Users/sachc/Projects/recall-ledger/.ledger/demo.sqlite", "mcp"]
    }
  }
}
```

**Local HTTP.** Run these commands in the project directory. Set `RECALL_TOKEN` in the client environment to the same value as the server. The built-in server binds to 127.0.0.1 and the selected database. It is a local integration endpoint. Network hosting, tenant authentication, TLS, rate limits and replication belong in a deployment layer.

```powershell
$env:RECALL_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m recall_ledger --db .ledger/demo.sqlite serve --port 8765
```

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/v1/lookup -Headers @{Authorization = "Bearer $env:RECALL_TOKEN"} -ContentType application/json -Body '{"key":"home.city"}'
```

Each named MCP operation also accepts a JSON object at `POST /v1/<operation>`, or from the CLI as `call <operation> --file request.json`. A client cannot override the server's database in its payload. The CLI and Python library also expose local administrative operations. Store the database in an OS-protected directory. The SQLite file contains plaintext evidence.

## Import and export

```powershell
python -m recall_ledger --db .ledger/account.sqlite import-provider mem0 mem0-page.json --scope user-821
python -m recall_ledger --db .ledger/account.sqlite import-provider supermemory supermemory-page.json --scope user-821
python -m recall_ledger --db .ledger/account.sqlite pending --role document
python -m recall_ledger --db .ledger/account.sqlite export --file account.jsonl
python -m recall_ledger --db .ledger/restored.sqlite restore account.jsonl
```

Mem0 inputs can be an array or a results page containing id, user_id, memory and an offset-aware updated_at or created_at. Supermemory inputs can be an array or a documents page with id, containerTags or containerTag, **full content**, and updatedAt or createdAt. Fetch document detail if a list export contains summaries only. The importer uses the upstream revision timestamp as the source effective time. Scope-mismatched rows are counted and skipped. In-scope malformed rows fail the page atomically. Replaying the same page is safe. Upstream IDs remain visible under source. Mem0 extraction output is preserved as a provider assertion. Importing cannot reconstruct a missing upstream transcript.

The native JSONL snapshot preserves source text, write IDs, review operations, effective dates, recording dates and the exact extracted assertions. The footer carries a record count and SHA-256 digest so restore can detect truncated or changed snapshots. Restore runs in one transaction into an empty workspace. Optional model vectors are rebuildable and excluded. The CLI export creates a new destination. Workspace erasure requires the absolute path again via `--confirm` and is available only locally. Historical expiry or revocation alone retains the source.

## Optional semantic passage search

With a local Ollama embedding model already available, index passages and query the resulting archive.

```powershell
python -m recall_ledger --db .ledger/demo.sqlite --model embeddinggemma index-vectors
python -m recall_ledger --db .ledger/demo.sqlite --model embeddinggemma search "relocation" --view hybrid_archive
```

The indexer discovers missing vectors from committed sources, validates each batch and can resume after a failure. New passages are immediately searchable via full text. Hybrid search combines lexical and cosine ranks using reciprocal rank fusion and reports the fraction of eligible passages indexed. Use an immutable model tag for consistent vector space. The dense path scans eligible vectors locally and is intended for modest corpora. Model quality depends on the installed model. [Ollama embedding API](https://docs.ollama.com/api/embed)

## Reproduce the evidence

```powershell
python -m unittest discover -s tests -v
python -m evals.run --out evals/local-results.json
python -m evals.scale --out evals/scale-results.json
```

The free-text replay gives every engine the same raw user messages in the same order. The local baseline uses SQLite BM25 over the transcripts. The fact and review-gated modes use Recall Ledger's offline parser and get no gold fields. The separate scale test supplies structured facts to isolate storage correctness. Both persist machine-readable results, including per-query text for the retrieval replay.

For a live comparison, use a disposable deployment and set `RECALL_BENCH_TOKEN` if its API requires authentication. The runner writes synthetic scopes with a unique prefix and leaves them available for inspection. Record the actual server version and model configuration with `--version`. Mem0 uses its OSS REST surface. Supermemory uses document ingestion, waits for indexing, then queries memory entries. The runner stops on transport or ingestion failures. A failed setup has no retrieval score.

```powershell
python -m evals.run --engine mem0-oss --url http://127.0.0.1:8888 --version "YOUR_VERSION_AND_MODELS" --out mem0-results.json
python -m evals.run --engine supermemory --url http://127.0.0.1:3000 --version "YOUR_VERSION_AND_MODELS" --out supermemory-results.json
```

These adapter contracts were checked with response fixtures. Published vendor benchmark scores use different datasets and evaluators. This repository's own measurements cover its local engine and transcript baseline. See the [research report](RESEARCH.md) for the measured failure cases, limits and proposed paid pilot.
