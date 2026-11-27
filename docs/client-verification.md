# Coding-client verification

Gravity Memory 0.4 was exercised on Windows using the installed clients below. Each client received its own disposable API key, temporary project and isolated configuration. The service ran on loopback. The configurations came from `python -m gravity_memory agent config CLIENT`. Set `GRAVITY_URL`, `GRAVITY_NAMESPACE`, and `GRAVITY_KEY` in the process that starts the client.

| Installed client | Native check | Observed result |
| --- | --- | --- |
| Claude Code 2.1.332 | Approved the project’s `.mcp.json` in the disposable profile and ran `claude mcp get gravity-memory`. | Claude reported **Connected**. Gravity received the authenticated tool-list request. A new project before approval showed **Pending approval**. |
| Codex CLI 0.172.0 | Loaded the generated TOML, checked `codex mcp get gravity-memory --json`, then called `mcpServerStatus/list` through the native Codex app-server. | Codex enabled the stdio entry, forwarded all three declared variables, and discovered **27 tools** for the test key. Gravity received the authenticated tool-list request. |
| OpenCode 2.1.4 | Loaded the generated configuration in a fresh project and checked `opencode mcp list` after startup. | OpenCode reported **connected**. Gravity received the authenticated tool-list request. |
| Cursor IDE 3.14.27 | Passed the generated stdio definition to Cursor’s native `--add-mcp` command with an isolated `--user-data-dir` and `--extensions-dir`. Read back the saved settings. | Cursor accepted `gravity-memory` and saved the command, arguments, type, and all three environment placeholders exactly. This check covers native configuration registration. A tool invocation inside a signed-in Cursor editor was not exercised. |

The executable regression test also launches the generated bridge commands and exchanges MCP messages with the team service. The client checks above do not require a model turn. [Regression tests](../tests/test_team.py)

## Version-specific setup

- **Claude Code** uses `${GRAVITY_KEY}` syntax in its project configuration. Approve the project server in Claude Code, then check the connection. [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
- **Codex** reads `env_vars` on the named server in TOML. Run the config generator using the same interpreter that has Gravity Memory installed. [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- **OpenCode v2** reads `mcp.servers.gravity-memory`. The older direct-under-`mcp` layout produced an empty server list on 2.1.4. Set the variables before starting the OpenCode background service. If that service already holds a previous URL or key, restart it with the new environment and recheck the listing. The first poll can finish before the connection is ready. [OpenCode MCP configuration](https://opencode.ai/docs/mcp-servers)
- **Cursor** accepts `type: "stdio"` and `${env:GRAVITY_KEY}` in its configuration. Save the generated JSON as `.cursor/mcp.json` for a project. Launch Cursor from the environment containing the team URL, namespace and key. [Cursor MCP documentation](https://cursor.com/docs/mcp)
