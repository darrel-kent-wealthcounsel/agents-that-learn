"""Shared word handling. viewer.html mirrors `query_tokens` in JavaScript, so keep them in step."""
import re

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
STOP = set(
    "a an the is are was were do does did where what which who how why when in on of to "
    "for from with and or we our i it this that be can should would there here my me us "
    "at by as if so into about".split()
)


def stem(t):
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    return t


def words(s):
    """startPayment -> ['start', 'payment'] (lower case, not stemmed)."""
    out = []
    for w in _WORD.findall(s):
        for p in _CAMEL.findall(w):
            p = p.lower()
            if len(p) >= 2 and not p.isdigit():
                out.append(p)
    return out


def tokens(s):
    return [stem(w) for w in words(s)]


def query_tokens(q):
    seen, out = set(), []
    for w in words(q):
        if w in STOP:
            continue
        t = stem(w)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
