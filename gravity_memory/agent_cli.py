"""Client configuration and a live round-trip through the namespaced memory API."""
import json
import sys
from pathlib import Path
from uuid import uuid4

from .client import GravityClient
from .service import stdio

ENV = ["GRAVITY_URL", "GRAVITY_NAMESPACE", "GRAVITY_KEY"]


def options(commands):
    agent = commands.add_parser("agent", help="connect a coding agent to a team namespace")
    actions = agent.add_subparsers(dest="action", required=True)
    actions.add_parser("mcp", help="bridge stdio MCP to the shared service")
    actions.add_parser("smoke", help="save, retrieve and cite a unique test decision")
    config = actions.add_parser("config", help="print a client configuration with no embedded credentials")
    config.add_argument("client", choices=["claude", "codex", "cursor", "opencode"])


def config(client):
    exe = sys.executable
    args = [] if getattr(sys, "frozen", False) else ["-m", "gravity_memory"]
    if sys.argv[0].endswith(".pyz"):
        args = [str(Path(sys.argv[0]).resolve())]
    args += ["agent", "mcp"]
    if client == "codex":
        return "\n".join(("[mcp_servers.gravity-memory]", "command = " + json.dumps(exe),
                          "args = " + json.dumps(args), "env_vars = " + json.dumps(ENV)))
    if client == "opencode":
        return {"mcp": {"servers": {"gravity-memory": {"type": "local", "command": [exe, *args],
                          "environment": {key: "{env:"+key+"}" for key in ENV}}}}}
    env = {key: ("${"+key+"}" if client == "claude" else "${env:"+key+"}") for key in ENV}
    return {"mcpServers": {"gravity-memory": {"type": "stdio", "command": exe, "args": args, "env": env}}}


def smoke(client):
    marker = "gravitysmoke"+uuid4().hex[:12]
    run = client.start_run("Agent connection test")
    rid = run["run_id"]
    body = "The connection marker is "+marker+"."
    client.call("remember", {"run_id": rid, "request_id": marker, "source": "Gravity connection test", "text": body,
                            "facts": [{"key": "setup."+marker, "value": marker, "quote": body}]})
    recall = client.recall(rid, marker)
    if not any(item["source_id"] == marker and item.get("value") == marker for item in recall["items"]):
        raise ValueError("the live memory did not return the saved marker")
    client.call("handoff", {"run_id": rid, "request_id": marker+"-closed", "summary": "Connection verified."})
    return {"verified": True, "run_id": rid, "trace_id": recall["trace_id"], "source_id": marker}


def execute(args):
    if args.action == "config":
        value = config(args.client)
        print(value if isinstance(value, str) else json.dumps(value, indent=2))
    elif args.action == "mcp":
        client = GravityClient()
        stdio(None, available=lambda: client.call("tools")["tools"],
              invoke=lambda _, op, values: client.call(op, values))
    else:
        print(json.dumps(smoke(GravityClient()), indent=2))
