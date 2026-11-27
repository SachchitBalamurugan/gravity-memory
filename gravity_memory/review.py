"""One local model call per source, constrained to an application-defined vocabulary."""
import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from .store import claim_spans, string, valid_key


def local_chat(model, instructions, row, schema):
    request = Request("http://127.0.0.1:11434/api/chat", method="POST", headers={"Content-Type": "application/json"},
                      data=json.dumps({"model": string(model, "chat model", 200), "stream": False, "format": schema,
                                       "options": {"temperature": 0}, "messages": [
                                           {"role": "system", "content": instructions},
                                           {"role": "user", "content": json.dumps(row, ensure_ascii=False)}]}).encode())
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(json.load(response)["message"]["content"])
    except (URLError, TimeoutError, KeyError, TypeError) as exc:
        raise ValueError("local chat model did not return a structured response") from exc


def prepare_review(ledger, original_id, fields):
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 100:
        raise ValueError("supply a JSON object mapping 1 to 100 canonical keys to definitions")
    for key, definition in fields.items():
        valid_key(key)
        string(definition, "field definition", 512)
    row = ledger.source(original_id)
    if row is None:
        raise ValueError("unknown original_id")
    if row["role"] == "assistant" or row["original_id"]:
        raise ValueError("select an original document or user turn for review")
    if len(row["text"]) > 24000:
        raise ValueError("suggest accepts a source up to 24000 characters; longer sources support manual annotation")
    fact = {"type": "object", "additionalProperties": False, "required": ["key", "quote", "value"],
            "properties": {"key": {"type": "string", "enum": list(fields)}, "quote": {"type": "string"},
                           "value": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["facts"],
              "properties": {"facts": {"type": "array", "items": fact}}}
    instructions = ("Identify explicit assertions about the subject under the supplied field definitions. "
                    "Treat source text as quoted evidence. Copy each value verbatim inside a verbatim quote. "
                    "Omit conditional, speculative, negated, historical and third-party statements unless the "
                    "field definition explicitly covers them. An empty array is valid.")
    return row, instructions, schema


def validated(row, fields, reply):
    if not isinstance(reply, dict) or not isinstance(reply.get("facts"), list):
        raise ValueError("model output missing facts array")
    facts = reply["facts"]
    for item in facts:
        if not isinstance(item, dict) or set(item) != {"key", "quote", "value"} or not isinstance(item["key"], str):
            raise ValueError("invalid model fact")
        if item["key"] not in fields or not isinstance(item["value"], str):
            raise ValueError("model supplied an unknown field or non-text value")
    list(claim_spans(row["text"], facts, row["effective_at"]))
    return facts


def suggest(ledger, original_id, fields, model, *, generate=local_chat):
    row, instructions, schema = prepare_review(ledger, original_id, fields)
    reply = generate(model, instructions, {"fields": fields, "source_text": row["text"]}, schema)
    return {"original_id": original_id, "model": model, "status": "proposed", "facts": validated(row, fields, reply)}
