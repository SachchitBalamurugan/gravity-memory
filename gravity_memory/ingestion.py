"""Atomic capture of conversation turns and user-supplied document contents."""
import hashlib
from datetime import datetime, timedelta

from .store import Conflict, encoded, string, utc


def _batch(items, name, maximum):
    if not isinstance(items, list) or not 1 <= len(items) <= maximum:
        raise ValueError(f"{name} must contain between 1 and {maximum} items")


def _counts(receipts):
    replayed = sum(item["replayed"] for item in receipts)
    return {"imported": len(receipts) - replayed, "replayed": replayed, "receipts": receipts}


def capture_conversation(ledger, request_id, source, messages):
    """Capture an immutable ordered batch. Use a new request ID for later turns."""
    string(request_id, "request_id", 256)
    string(source, "source", 512)
    _batch(messages, "messages", 200)
    prefix = "conversation/" + hashlib.sha256(request_id.encode()).hexdigest() + "/"
    turns = {}
    for index, message in enumerate(messages, 1):
        if not isinstance(message, dict) or message.keys() - {"role", "content", "effective_at"}:
            raise ValueError(f"message {index} must contain role, content and an optional effective_at")
        role = message.get("role")
        if role not in ("assistant", "user"):
            raise ValueError(f"message {index} role must be user or assistant")
        body = string(message.get("content"), f"message {index} content", 2_000_000)
        when = message.get("effective_at")
        if when is not None:
            when = utc(when)
        # A replay that drops a supplied timestamp must conflict even if the original
        # effective time happens to match a generated time.
        turn_id = f"{prefix}{index:03d}/" + ("dated" if when is not None else "ordered")
        turns[turn_id] = (role, body, when)

    with ledger._tx(write=True) as db:
        previous = dict(db.execute("SELECT request_id,effective_at FROM documents WHERE request_id GLOB ?",
                                   (prefix + "*",)))
        if previous and previous.keys() != turns.keys():
            raise Conflict("conversation request_id already belongs to a different batch")
        recorded = utc()
        captured = datetime.fromisoformat(recorded)
        receipts = []
        for index, (turn_id, (role, body, when)) in enumerate(turns.items(), 1):
            if when is None:
                # Missing event dates follow transcript order up to capture time.
                # All turns become known together. Persist their first dates on retry.
                when = previous.get(turn_id) or (captured - timedelta(microseconds=len(turns) - index)).isoformat()
            receipts.append(ledger._remember(db, turn_id, body, source, role, when, None, recorded))
        return _counts(receipts)


def import_documents(ledger, files):
    """Archive UTF-8 text/Markdown supplied by the client, preserving the original text."""
    _batch(files, "files", 100)
    receipts = []
    with ledger._tx(write=True) as db:
        recorded = utc()
        for index, entry in enumerate(files, 1):
            if not isinstance(entry, dict) or entry.keys() - {"name", "text", "effective_at"}:
                raise ValueError(f"file {index} must contain name, text and an optional effective_at")
            name = string(entry.get("name"), f"file {index} name", 255)
            if any(c in name for c in ("/", "\\", ":")) or any(ord(c) < 32 for c in name):
                raise ValueError(f"file {index} name must be a filename without a directory")
            if not name.lower().endswith((".txt", ".md", ".markdown")):
                raise ValueError(f"file {index} must be a .txt, .md or .markdown document")
            body = string(entry.get("text"), f"file {index} text", 2_000_000)
            when = entry.get("effective_at")
            if when is not None:
                when = utc(when)
            digest = hashlib.sha256(encoded([name, body, when]).encode()).hexdigest()
            receipts.append(ledger._remember(db, "file/" + digest, body, name, "document", when, [], recorded))
        return _counts(receipts)
