# Gravity Memory

Gravity Memory helps AI agents remember facts between sessions. Each fact points back to its source. When someone corrects it, the next agent can use the new value, and you can still see what an earlier agent knew.

For example, a customer tells a support agent they moved from Seattle to Tucson. An older conversation about Reno gets imported later. Gravity still returns **Tucson** as the current city, **Seattle** for a January lookup, and the original message behind each answer.

## Try it locally

**On Windows**, download and open **GravityMemory.exe**. It opens the console in your browser and stores your personal workspace on this computer. [Download the latest release](https://github.com/SachchitBalamurugan/gravity-memory/releases/latest)

**From source**, run the same city-change example with Python 3.11 or newer and SQLite with FTS5. The demo uses a temporary database. It runs offline without a model or API key.

```sh
git clone https://github.com/SachchitBalamurugan/gravity-memory.git
cd gravity-memory
python -m gravity_memory demo
```

To open the console from that checkout, run `python -m gravity_memory studio`. Choose **Agents & MCP → Run verification** to save a fact, recall it, and inspect the source your agent actually received.

![Gravity Memory console showing agent runs, memory reads and writes, namespaces, and recent searches](docs/overview.png)

## Use it with your agents

| What you want to do | Where to start |
| --- | --- |
| Give a Python or TypeScript app memory across sessions | [Python quickstart](docs/team-service.md#connect-an-agent) · [TypeScript quickstart](sdk/typescript/README.md) |
| Share a decision between Claude Code, Codex, Cursor, or OpenCode | [Coding-agent setup](docs/team-service.md#connect-an-agent) · [Handoff example](examples/coding-handoff.mjs) |
| Find out why an agent used an outdated fact | [Trace and replay a customer correction](examples/customer_correction.py) |
| Host a team workspace with separate access for each project | [Self-host with Docker Compose](docs/team-service.md#start-the-service) |
| Sync selected private GitHub, Notion, or Google Drive content | [Connector setup](docs/private-connectors.md) |
| Use local models, import memories, or move a workspace | [Local CLI and storage reference](docs/local-reference.md) |

## Development

Run `python -m unittest discover -s tests -v` from the checkout. The evaluation report includes the cross-session tasks, expected citations, measured latency, and instructions for reproducing the results. [Evaluation report](docs/agent-evaluation.md)

## License

Gravity Memory and its SDKs are available under the Apache License, Version 2.0. Third-party software and model weights retain their respective licenses. [Apache 2.0 license](LICENSE)
