"""Run in a disposable team namespace with read, write and feedback access."""
from uuid import uuid4
from gravity_memory.client import GravityClient

def main():
    client = GravityClient()
    prefix = uuid4().hex[:10]
    key = "contact.channel"
    metadata = {"customer": prefix}
    scope = {"field": "metadata.customer", "operator": "eq", "value": prefix}
    def preference(label, value):
        text = f"Please contact me via {value}."
        return client.call("remember", {"request_id": prefix+label, "text": text, "source": "Customer conversation",
                            "metadata": metadata, "facts": [{"key": key, "value": value, "quote": text}]})
    previous = client.start_run("Support visit")
    preference("-first", "email")
    saved = client.recall(previous["run_id"], "contact channel", filter=scope)
    client.call("handoff", {"run_id": previous["run_id"], "request_id": prefix+"-handoff", "summary": "Verify the current contact channel before replying."})
    preference("-corrected", "Signal")
    next_visit = client.start_run("Returning customer", parent_run_id=previous["run_id"])
    current = client.recall(next_visit["run_id"], "contact channel", filter=scope)
    assert saved["items"][0]["value"] == "email"
    assert current["items"][0]["source_id"] == prefix+"-corrected"
    replay = client.call("compare_trace", {"trace_id": saved["trace_id"]})
    assert replay["recorded"]["items"][0]["value"] == "email" and replay["current"]["items"][0]["value"] == "Signal"
    client.call("outcome", {"run_id": next_visit["run_id"], "request_id": prefix+"-outcome", "success": True, "detail": "Reply routed to the corrected channel."})
    client.call("handoff", {"run_id": next_visit["run_id"], "request_id": prefix+"-end", "summary": "Verified the contact correction."})
    print("Current channel:", current["items"][0]["value"], "Original trace:", saved["trace_id"])

if __name__ == "__main__":
    main()
