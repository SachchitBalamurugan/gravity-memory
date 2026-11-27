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
# Context is part of the fact key. Instructions for a shift or day of the
# week must not overwrite an unconditional contact preference.
CONTACT_CONTEXTS = {
    "on_call": r"on[- ]call",
    "outside_work": r"outside (?:of )?work|after[- ]hours",
    "at_work": r"at work|during (?:work|business) hours",
    "weekends": r"(?:on )?weekends",
    "weekdays": r"(?:on )?weekdays",
}
CHANNEL_NAMES = r"(?:e-?mail|phone|text|sms|slack|signal|matrix|pagerduty|teams|whatsapp|telegram|discord|pager)"
for name, condition in CONTACT_CONTEXTS.items():
    condition = r"(?:(?:when|while) I(?: am|'m|’m) )?(?:" + condition + r")"
    explicit = r"(?:please )?(?:(?:contact|reach|message) me (?:via|by|on|using)|my preferred contact channel is) "
    shorthand = r"(?:I prefer|please use|use) (?P<value>" + CHANNEL_NAMES + r")"
    PATTERNS["contact.channel." + name] = [
        condition + r"\s*,?\s+" + explicit + VALUE,
        condition + r"\s*,?\s+" + shorthand,
        explicit + VALUE + r"\s+" + condition,
        shorthand + r"\s+" + condition,
    ]

for suffix, verb in [("home.city", "lives in"), ("work.employer", "works (?:at|for)")]:
    PATTERNS["relation." + suffix] = [rf"My (?P<relation>{RELATION}) {verb} " + VALUE]
RULES = [(key, re.compile(pattern, re.I)) for key, patterns in
         sorted(PATTERNS.items(), key=lambda item: not item[0].startswith("contact.channel."))
         for pattern in patterns]
HINTS = {
    "city": {"live", "lives", "residence", "reside", "city", "moved", "based"},
    "employer": {"employer", "company", "employed", "workplace", "employs"},
    "timezone": {"timezone", "zone", "tz"},
    "channel": {"contact", "contacted", "reached", "reach", "channel"},
    "name": {"name", "called"},
}
UNCERTAIN = re.compile(
    r"\b(?:not|never|maybe|might|sometimes|usually|until|except|when|while|if|or|and|but|"
    r"since|because|formerly|previously|temporarily|next|soon|before|after|during|part.time|"
    r"last|tomorrow|yesterday|today|only|on (?:weekdays|weekends|mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays))\b", re.I)
STOP = set("a an the to do does i my me our is are was where what which how can has have did am in at for of on be we you user current currently preferred please their they it now tell get s".split())


def tokens(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def contact_scope(query):
    canonical = re.fullmatch(r"contact\.channel\.(\w+)", query.strip(), re.I)
    if canonical and canonical[1].lower() in CONTACT_CONTEXTS:
        return canonical[1].lower()
    if re.search(r"\b(?:not|never)\s+(?:at work|on[- ]call|on (?:weekdays|weekends))\b", query, re.I):
        return "ambiguous"
    matches = [name for name, pattern in CONTACT_CONTEXTS.items()
               if re.search(r"\b(?:" + pattern + r")\b", query, re.I)]
    if len(matches) > 1:
        return "ambiguous"
    return matches[0] if matches else None


def matches_contact_scope(key, scope):
    if key == "contact.channel":
        return scope is None
    prefix = "contact.channel."
    if key.startswith(prefix) and key[len(prefix):] in CONTACT_CONTEXTS:
        return key == prefix + str(scope)
    return True


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
            if key.startswith("contact.channel") and contact_scope(match["value"]) is not None:
                continue
            relation = match.groupdict().get("relation")
            if relation:
                key = key.replace("relation.", f"relation.{relation.lower()}.", 1)
            start = text.find(phrase, sentence.start(), sentence.end())
            facts.append({"key": key, "value": match["value"], "quote": phrase, "start": start})
            break
    return facts
