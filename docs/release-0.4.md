# Gravity Memory 0.4

Open **GravityMemory.exe** on Windows x64 for the desktop console. Use the Python wheel or the source archive for a shared installation. Choose **Agents & MCP** to run the first verification in your workspace. Adding a source, recalling it, and inspecting the resulting trace works without a language model.

### What changed

- A dark console now opens on namespace activity. Follow a read from Overview to its run, inspect the original evidence, then compare it with a fresh recall after a correction. The graph, library, model settings, review queue and imports share the same interface.
- An agent can pass an explicit handoff between sessions, filter eligible sources before ranking, get a response that fits its byte budget and cite the original. Administrators can approve, reject or retract durable lessons. Read-only keys can request standalone recall.
- One team can host the console over HTTPS with built-in accounts, isolated namespace databases, revocable keys and backup and restore. Selected private GitHub repositories, Notion pages and Google Drive items can publish to a namespace. [Team guide](team-service.md) · [Connector setup](private-connectors.md)

### How customer reports affected the build

One developer described going back to Markdown and grep because diagnosing wrong recall consumed more time than the feature saved. A local-agent discussion raised the case of a semantically relevant configuration that had become obsolete. These reports led to two acceptance tasks: inspect the exact previous result, and demonstrate that a later agent using the same query can safely choose the corrected source. These are firsthand reports and simulated workflows. Willingness to pay has not been measured. [Developer discussion](https://www.reddit.com/r/AI_Agents/comments/1x8z0tj/how_are_you_handling_longterm_memory_for_your/) · [Local stale-memory discussion](https://www.reddit.com/r/LocalLLM/comments/1xz4f8p/how_do_you_handle_outdated_memories_in_local_ai/)

A Mem0 operator found assistant output being stored as personal knowledge. A Supermemory integration issue described a mismatch between search results and the field the client displayed. Gravity retains speaker roles, requires a cited original for a durable lesson, and tests the entire trip from a browser-issued key through a real SDK to a visible citation. [Mem0 operator report](https://github.com/mem0ai/mem0/issues/4573) · [Supermemory adapter report](https://github.com/supermemoryai/claude-supermemory/issues/81)

REA inspection of the published competitor SDKs established their profile, history, search and connector interfaces. For this release, REA 6.10.1 also inspected `index.js` extracted from the distributable TypeScript tarball. Its directory identity was `03666c540b856433d33d84b505eaba14160979cb6bca9a599b10b54b86b62c1e`, evidence `ev_9b048d100fc0b18c7ecc756794653dff85f66f1a168e430d340446182717c23b`. Separate live tests exercised the helper methods and the browser run inspector. [Earlier SDK evidence](rea-evidence.json) · [Native client results](client-verification.md)

### Measured behavior

The 0.3 release selected the expected action and source on 14/24 synthetic two-session scenarios. The first 0.4 build reached 23/24. The remaining failure confused a runtime with a requested build command. After updating ranking and testing three additional phrasings, the release reached 24/24 on the original set. Its median local recall was 8.13 ms, including persistent tracing, compared with 3.08 ms on 0.3. No matched hosted Mem0 or Supermemory endpoint was available for a head-to-head test. [Evaluation and reproduction instructions](agent-evaluation.md)

Verification includes a trusted TLS connection to the Compose gateway, a container restart with the original data intact, Windows and Python packaging, SDK and MCP round trips, real private GitHub reads, and browser journeys on desktop and mobile. Notion and Google Drive used simulated provider responses. Cursor accepted the generated configuration in an isolated profile. A signed-in Cursor tool call remains unverified. [Client verification](client-verification.md) · [Connector verification](private-connectors.md)
