"""Deliberately narrow offline extraction and lexical query expansion."""
import re
import unicodedata

KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,7}")
PATTERNS = {
    "home.city": r"(?:I (?:now )?live in|I (?:just )?moved to|My home city is)",
    "work.employer": r"(?:I (?:now )?work (?:at|for)|My employer is)",
    "profile.timezone": r"(?:My (?:time ?zone) is)",
    "contact.channel": r"(?:My preferred contact (?:channel|method) is|Please contact me (?:via|by))",
    "profile.name": r"(?:My name is)",
}
HINTS = {
    "home.city": {"live", "residence", "reside", "hometown", "city", "moved", "based"},
    "work.employer": {"employer", "company", "employed", "workplace"},
    "profile.timezone": {"timezone", "zone", "tz"},
    "contact.channel": {"contact", "reach", "channel"},
    "profile.name": {"name", "called"},
}
UNCERTAIN = re.compile(
    r"\b(?:not|never|maybe|might|sometimes|usually|until|except|when|while|if|or|and|but|"
    r"since|because|formerly|previously|temporarily|next|soon|before|after|during|part.time|"
    r"on (?:weekdays|weekends|mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays))\b", re.I
)
STOP = set("a an the to do does i my me our is are was where what which how can has have did am in at for of on be we you user current currently preferred please their they it now tell get s".split())


def tokens(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def search_terms(query, expand=False):
    if not isinstance(query, str) or len(query) > 2048:
        raise ValueError("query must be text up to 2048 characters")
    terms = [t for t in tokens(query) if t not in STOP][:40]
    if expand:
        for key, hints in HINTS.items():
            if hints.intersection(terms):
                terms.extend(key.split(".")[-1:])
    return list(dict.fromkeys(terms))


def extract(text):
    facts = []
    # Only an entire declarative sentence may establish a fact. Questions,
    # subordinate clauses and compound or hedged values remain in the archive.
    for sentence in re.finditer(r"(?:^|(?<=[.!?\n]))[ \t]*([^.!?\n]+)([.!?]|(?=\n|$))", text):
        if sentence[2] == "?":
            continue
        phrase = sentence[1].strip()
        for key, prefix in PATTERNS.items():
            match = re.fullmatch(prefix + r" ([\w][\w /'-]{0,64})", phrase, re.I)
            if match and not UNCERTAIN.search(match[1]) and len(match[1].split()) <= 5:
                facts.append({"key": key, "value": match[1].strip(), "quote": phrase})
                break
    return facts
