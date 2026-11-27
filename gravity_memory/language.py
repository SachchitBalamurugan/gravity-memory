"""Narrow offline extraction for explicit, attributed statements."""
import re
import unicodedata

KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,7}")
RELATIONS = "partner spouse sister brother husband wife mother father manager roommate colleague son daughter".split()
RELATION = "|".join(RELATIONS)
VALUE = r"(?P<value>[\w][\w /'’-]{0,64}?)"
PATTERNS = {
    "home.city": [r"(?:I (?:now )?live in|I (?:just )?moved to|My home city is) " + VALUE,
                  r"(?:These days |Now )?I call " + VALUE + r" home"],
    "work.employer": [r"(?:I (?:now )?work (?:at|for)|My employer is) " + VALUE],
    "profile.timezone": [r"My time ?zone is " + VALUE],
    "contact.channel": [r"(?:My preferred contact (?:channel|method) is|Please contact me (?:via|by)) " + VALUE],
    "profile.name": [r"My name is " + VALUE],
}
for suffix, verb in [("home.city", "lives in"), ("work.employer", "works (?:at|for)")]:
    PATTERNS["relation." + suffix] = [rf"My (?P<relation>{RELATION}) {verb} " + VALUE]
RULES = [(key, re.compile(pattern, re.I)) for key, patterns in PATTERNS.items() for pattern in patterns]
HINTS = {
    "city": {"live", "lives", "residence", "reside", "city", "moved", "based"},
    "employer": {"employer", "company", "employed", "workplace", "employs"},
    "timezone": {"timezone", "zone", "tz"},
    "channel": {"contact", "reach", "channel"},
    "name": {"name", "called"},
}
UNCERTAIN = re.compile(
    r"\b(?:not|never|maybe|might|sometimes|usually|until|except|when|while|if|or|and|but|"
    r"since|because|formerly|previously|temporarily|next|soon|before|after|during|part.time|"
    r"last|tomorrow|yesterday|today|only|on (?:weekdays|weekends|mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays))\b", re.I)
STOP = set("a an the to do does i my me our is are was where what which how can has have did am in at for of on be we you user current currently preferred please their they it now tell get s".split())


def tokens(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def subject_scope(query):
    match = re.search(r"\b(?:my|our|the user's|the user’s)\s+(" + RELATION + r")\b", query, re.I)
    if match:
        return "relation." + match[1].lower() + "."
    if re.search(r"\b(?:I|my|me|myself)\b", query, re.I):
        return "self"
    return None


def search_terms(query, expand=False):
    if not isinstance(query, str) or len(query) > 2048:
        raise ValueError("query must be text up to 2048 characters")
    terms = [t for t in tokens(query) if t not in STOP][:40]
    if expand:
        for field, hints in HINTS.items():
            if hints.intersection(terms):
                terms.append(field)
    return list(dict.fromkeys(terms))


def extract(text):
    facts = []
    # Keep punctuation inside ordinary abbreviations/domains out of partial claims.
    # An unsupported whole value goes to the operator for review.
    masked = re.sub(r"\b(?:St|Mt|Ft|Dr|Mr|Ms|Mrs)\.(?=\s+\w)|(?<=\w)\.(?=\w)", lambda m: m[0].replace(".", "·"), text, flags=re.I)
    for sentence in re.finditer(r"[^.!?\n]+(?:[.!?]|$|(?=\n))", masked):
        phrase = text[sentence.start():sentence.end()].strip()
        if phrase.endswith("?"):
            continue
        phrase = phrase.rstrip(".!").strip()
        for key, pattern in RULES:
            match = pattern.fullmatch(phrase)
            if not match or UNCERTAIN.search(match["value"]) or len(match["value"].split()) > 5:
                continue
            relation = match.groupdict().get("relation")
            if relation:
                key = key.replace("relation.", f"relation.{relation.lower()}.", 1)
            start = text.find(phrase, sentence.start(), sentence.end())
            facts.append({"key": key, "value": match["value"], "quote": phrase, "start": start})
            break
    return facts
