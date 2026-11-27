# Run a shared Gravity Memory workspace

## Start the service

Use a local disk for SQLite and its WAL files. The Compose deployment runs Gravity Memory as an unprivileged user and exposes the Caddy HTTPS gateway. On a Linux host, create a private secrets directory and generate the initial owner's password. Store that password in your password manager. The container user has UID 10001 and needs read access to the bootstrap file.

```sh
mkdir -m 700 secrets
(umask 077 && openssl rand -base64 32 > secrets/owner-password)
sudo chown -R 10001:10001 secrets
GRAVITY_HOST=memory.example.com docker compose up --build -d
```

Point the chosen hostname to your host and allow ports 80 and 443 for Caddy certificate issuance. For local Docker Desktop, create the same `secrets/owner-password` file and run `docker compose up --build -d`. Caddy uses its local CA for `localhost`. To trust it in your local client, copy the certificate with `docker compose cp gateway:/data/caddy/pki/authorities/local/root.crt gravity-local-ca.crt` and import it into that client's trust store. The owner username defaults to `owner`. The service reads the bootstrap password only when creating the first account.

To run the identical team service directly on loopback, install the source checkout and start it with a bootstrap secret from your shell or secret manager.

```sh
python -m pip install ".[connectors]"
gravity-memory team serve --directory ./team-data
```

Set `GRAVITY_OWNER_PASSWORD` or `GRAVITY_OWNER_PASSWORD_FILE` before first start. Open the printed loopback URL, sign in, and create a namespace in **Namespaces**. The team owner creates accounts. Namespace administrators assign admin, member or viewer access and issue agent keys. The owner can access every namespace. A newly created account sees only its explicit grants.

## Connect an agent

In **API keys**, issue a key for the selected namespace. Load `GRAVITY_URL`, `GRAVITY_NAMESPACE`, and `GRAVITY_KEY` into the environment of the application or editor. Keys are shown once. Choose read and write for a save-and-recall smoke test. Give feedback permission to an agent that reports outcomes. Enable trusted reporting only for an authoritative source such as a verified deployment or test runner.

```sh
gravity-memory agent smoke
gravity-memory agent config claude
gravity-memory agent config codex
gravity-memory agent config cursor
gravity-memory agent config opencode
```

Merge the printed entry into the target client's existing config. Use `.mcp.json` for Claude Code, `~/.codex/config.toml` for Codex, `.cursor/mcp.json` for Cursor, and `opencode.json` for OpenCode. Restart the client with the three environment variables available. The generated stdio entry launches the installed interpreter or executable and connects to the chosen team's HTTPS origin. Each key sees only its permitted tools. A second agent can start a child run after the first records a handoff.

```python
from gravity_memory.client import GravityClient

memory = GravityClient()
run = memory.start_run("Prepare support reply")
context = memory.recall(run["run_id"], "preferred contact channel", budget_bytes=2048)
for evidence in context["items"]:
    print(evidence["source"], evidence["quote"])
print(context["status"], context["trace_id"])
```

Use stable request IDs when retrying. For TypeScript, install the release tarball or run `npm install ./sdk/typescript` from the source checkout. With read, write and feedback keys in a disposable namespace, run `python -m examples.customer_correction` and `node examples/coding-handoff.mjs`. The first replays a customer correction. The second hands a changed repository decision to a new run and can use a second agent key via `GRAVITY_PEER_KEY`. [TypeScript client](../sdk/typescript/README.md)

## Retrieval, learning and tracing

A read-only key can call `memory.recall(None, query)` in Python or `client.recall(null, query)` in TypeScript. The request appears under Latest searches. Supply an open, owned `run_id` to ordinary calls such as `remember` or `search` to attach them to a run's timeline. Notes and outcomes can include agent-reported `telemetry` for tool, model, route, fallback reason, latency, token counts, or cost. The trace distinguishes reported values from timings measured by Gravity.

Use `POST /v2/namespaces/{namespace}/{operation}` with a Bearer key and a JSON object. `tools` returns the permitted operations and their argument schemas. The lifecycle is `start_run`, `run_note`, `recall`, `outcome`, and `handoff`. Use `run_detail` and `trace` to inspect recorded inputs and outputs, and `compare_trace` to retrieve fresh evidence with the original query settings. The return status distinguishes grounded facts, passages that require interpretation, and abstention. A hard byte budget includes the compact UTF-8 recall envelope and its citations.

`remember` accepts scalar metadata. Search, context, profile, graph and agent recall accept nested filters. For example, use `{"operator":"and","operands":[{"field":"metadata.project","operator":"eq","value":"checkout"},{"field":"metadata.priority","operator":"gte","value":3}]}`. Namespace authorization precedes retrieval. The filter restricts the eligible evidence before ranking.

Run notes are available immediately. Proposed lessons cite an original and enter **Review**. A namespace administrator can allow selected lesson types to be promoted after a trusted successful outcome or corroboration from independent successful runs. Human-confirmed conflicts require review. Approvals and retractions preserve evidence of the decision. When you report a tool result, link the source IDs to the note or outcome so hard erasure can clear it later.

Authorized memory requests, responses and evidence are retained until deletion by default. The console shows retained trace bytes and optional expiry by namespace. Credential issuance, passwords, authentication headers and provider credentials travel outside the trace recorder. Content an application intentionally puts in a memory or tool report can appear in its traces. Every reader of a namespace can inspect those records. Set the sharing boundary before importing content. Historical replay reproduces the saved response. Hard erasure removes linked originals, derivations and affected trace payloads. Independently written free text and offline backups have independent lifecycles. [Connector setup and access boundaries](private-connectors.md)

## Backup, restore and desktop migration

Stop the service for a consistent whole-team snapshot. The backup includes accounts, permissions, hashed agent keys, originals, histories and traces. Protect it like the original data. Restore verifies the checksums and database integrity, creates a new directory, and signs out existing browser sessions. Connector credentials and Caddy certificates remain in their separately managed secrets directory and volume.

```sh
gravity-memory team backup --directory ./team-data --to ./backup-01
gravity-memory team restore --from ./backup-01 --directory ./restored-data
gravity-memory team migrate --directory ./restored-data --source ./personal.sqlite --namespace imported-personal
gravity-memory team serve --directory ./restored-data
```

For Compose, stop the service with `docker compose stop memory`, mount a writable backup directory, and run `docker compose run --rm --no-deps -v "$PWD/backups:/backup" memory team backup --directory /data --to /backup/snapshot-01`. Start it again with `docker compose start memory`. Restore to a fresh directory or volume before switching your deployment. A migrated local database gets a new private namespace with no additional member grants, and the original desktop database is preserved.
