# Gravity Memory 0.3

The console is organized around three operator questions. **Profile** shows which facts apply to this workspace at a selected date. **Context explorer** shows what an agent would receive for a query. **Import data** brings in new evidence. The design uses original layouts, a light working surface, graphite navigation and the Eclipse amber accent.

## From a conversation to an agent response

```python
from gravity_memory import GravityMemory

memory = GravityMemory("account-42.sqlite")
receipt = memory.capture_conversation("handoff-42", "Support handoff", [
    {"role": "user", "content": "Please contact me via email."},
    {"role": "assistant", "content": "Should the on-call team use email too?"},
    {"role": "user", "content": "On call I prefer PagerDuty."},
])
profile = memory.profile()
response = memory.context("How should I be contacted on call?", retrieval="auto")
assert response["facts"][0]["value"] == "PagerDuty"
assert memory.lookup("contact.channel")["value"] == "email"
```

**Profile** groups resolved current facts by canonical key prefix, reports excluded states and exposes original citations. It accepts subject, effective-time and knowledge-time filters. **Context** reads the profile, ranked current facts, and original passages in one SQLite snapshot, with the actual retrieval route. Full text works immediately. Semantic retrieval requires a compatible local index. Passages describe the historical record and may mention other people or superseded facts.

**Evidence connections** show the original sources cited for a key, and other keys recorded in the same sources. A shared source establishes co-occurrence. Its contents determine whether the two facts have a meaningful real-world relationship. Related historical assertions can include retired facts. **Retire memory** requires an operator reason, excludes that key from the current profile at the effective time, and preserves historical reads.

**Conditional contact preferences** cover explicit English declarations for on-call duty, outside work, at work, weekends and weekdays. General and conditional channels can coexist. Complex exceptions, vague requests and unknown condition types may require a human or explicit application-defined keys.

## Capture and import

| Input | Behavior |
|---|---|
| Conversation | Supply a stable batch ID, a descriptive source and 1–200 ordered user/assistant turns. Each turn can have its own effective date. A retry is idempotent. A batch with modified content under the same ID conflicts. Assistant turns are archived. |
| Documents | Import up to 100 UTF-8 text or Markdown files per batch. Names, original text, exact quote positions and optional dates are preserved. Matching content, name and supplied date replay safely. |
| Mem0 | Import a list or a results page scoped to the intended user. If every row omits the user identifier, attach the exact request's user_id to the saved page. Provider summaries enter the source archive for review. |
| Supermemory | Import a scoped documents page. For v5, request full document content and retain the namespace used in the original request. Add it as a top-level namespace when the API omits it from the response. Other tenants' data is skipped. |
| GitHub file | Connect a public repository and an individual text, Markdown or reStructuredText file (up to 900 KB). The default branch, a named branch/tag, or a commit can be selected. Sync checks the Git blob hash and records an immutable source. Refreshing unchanged content adds zero sources. |

GitHub sync is on demand. Only the selected repository, path and revision are requested from GitHub. The connection does not send workspace memories or require an account token. Disconnecting preserves imported documents. A complete workspace erase also removes its connections.

All agent operations are available in Python, via the local authenticated HTTP API, MCP and the generic CLI command. For example, an agent can submit a JSON file to `gravity-memory --db account.sqlite call context --file query.json`. Networked GitHub connections are exposed in the studio and through the explicit CLI commands:

```powershell
python -m gravity_memory --db account.sqlite github connect mem0ai/mem0 README.md --ref main
python -m gravity_memory --db account.sqlite github list
python -m gravity_memory --db account.sqlite github sync CONNECTION_ID
```

## What REA established

I applied the reverse-engineer-anything skill from revision `366e1d98c1b3ba31e7f9e33cd8779713f86b3099` and used REA CLI 6.10.1 to inspect selected public, published SDK artifacts. Supermemory 5.1.2 exposed profile bucket methods, batch documents, and connector create/list/sync. Mem0 3.3.1 exposed conversation add/search, history, profile generation/read, updates, and export. These are observations about client interfaces. Hosted ranking behavior, server implementation and performance cannot be measured from these SDKs. [REA skill](https://github.com/morluto/rea/blob/366e1d98c1b3ba31e7f9e33cd8779713f86b3099/.agents/skills/reverse-engineer-anything/SKILL.md) · [Artifact identities and exact method locations](rea-evidence.json)

We used those workflows to design a local profile, combined retrieval, import, connection and correction experience. Gravity Memory uses a keyed SQLite timeline, bounded evidence links and an operator-reviewed model workflow. Private SaaS integrations, background OAuth synchronization, multimodal ingestion and managed multi-tenant hosting are outside this release. [Supermemory profile documentation](https://supermemory.ai/guides/user-profiles-llm-personalization/) · [Mem0 extraction and retrieval documentation](https://github.com/mem0ai/mem0/blob/main/docs/migration/oss-v2-to-v3.mdx)

## Measured behavior

On the existing correction workload, default top-one recall rose from **12/18 to 14/18** after adding bounded contact contexts. Obsolete returns stayed at **0/11** and unknown queries returned empty in **9/9** cases. This workload was available during development. It measures regression behavior, not comparative vendor performance. A live public GitHub README was imported, synced unchanged, and retrieved by full-text search with one original in the database. [Current replay](../evals/platform-results.json) · [Earlier replay](../evals/gravity-results.json)
