# grouping and sanity checks for the web app
# wraps verses.parse_refs so the command line and the web page share one cleaner
#
# input format (one group per block, blank line between groups):
#   The sense of death: John 4:13; Isa. 55:2; Luke 15:14-16
#   Heb. 12:3; Luke 8:6
#
# a heading is a line (or the start of a line before a colon) with words and no digits
# that is not a book name. lines under a heading belong to it until a blank line.

import re

from verses import estimate, lookup_book, parse_refs

# chapters per book, keyed by the recommended abbreviation used in verses.BOOKS
CHAPTERS = {
    "Gen.": 50, "Exo.": 40, "Lev.": 27, "Num.": 36, "Deut.": 34, "Josh.": 24, "Judg.": 21,
    "Ruth": 4, "1 Sam.": 31, "2 Sam.": 24, "1 Kings": 22, "2 Kings": 25, "1 Chron.": 29,
    "2 Chron.": 36, "Ezra": 10, "Neh.": 13, "Esth.": 10, "Job": 42, "Psa.": 150, "Prov.": 31,
    "Eccl.": 12, "S.S.": 8, "Isa.": 66, "Jer.": 52, "Lam.": 5, "Ezek.": 48, "Dan.": 12,
    "Hosea": 14, "Joel": 3, "Amos": 9, "Oba.": 1, "Jonah": 4, "Micah": 7, "Nahum": 3,
    "Hab.": 3, "Zeph.": 3, "Hag.": 2, "Zech.": 14, "Mal.": 4, "Matt.": 28, "Mark": 16,
    "Luke": 24, "John": 21, "Acts": 28, "Rom.": 16, "1 Cor.": 16, "2 Cor.": 13, "Gal.": 6,
    "Eph.": 6, "Phil.": 4, "Col.": 4, "1 Thes.": 5, "2 Thes.": 3, "1 Tim.": 6, "2 Tim.": 4,
    "Titus": 3, "Philem.": 1, "Heb.": 13, "James": 5, "1 Pet.": 5, "2 Pet.": 3, "1 John": 5,
    "2 John": 1, "3 John": 1, "Jude": 1, "Rev.": 22,
}

MAX_VERSE_ESTIMATE = 400   # keeps one request from burning through the api quota


def looks_like_heading(text):
    # words only, at least two letters, no digits, and not a book name on its own
    text = text.strip()
    if not text or re.search(r"\d", text):
        return False
    if len(re.findall(r"[A-Za-z]", text)) < 3:
        return False
    return lookup_book(text) is None


def split_heading(line):
    # returns (heading, rest), heading is "" when the line is plain references
    line = line.strip()
    if line.startswith("#"):
        return line.lstrip("# ").strip(), ""
    m = re.match(r"^([^:;,\d]{3,80}?)\s*:\s*(.*)$", line)
    if m and looks_like_heading(m.group(1)):
        return m.group(1).strip(), m.group(2).strip()
    if looks_like_heading(line):
        return line.rstrip(":").strip(), ""
    return "", line


def check_ref(ref):
    # returns a problem description for a cleaned reference, or None when it looks fine
    m = re.fullmatch(r"(.+?) (\d+)(?::(\d+))?(?:-(\d+)(?::(\d+))?)?", ref)
    if not m:
        return None
    book = m.group(1)
    limit = CHAPTERS.get(book)
    if limit is None:
        return None
    first = int(m.group(2))
    if book in ("Oba.", "Philem.", "2 John", "3 John", "Jude"):
        return None
    if first < 1 or first > limit:
        return "%s only has %d chapters" % (book, limit)
    if m.group(3) and m.group(4) and not m.group(5):
        if int(m.group(4)) < int(m.group(3)):
            return "the verse range runs backwards"
    return None


def parse_groups(text):
    # returns (groups, warnings), groups is a list of {"heading", "refs"}
    groups = []
    warnings = []
    cur = None

    def flush():
        nonlocal cur
        if cur is None:
            return
        if cur["lines"]:
            refs, warns = parse_refs("\n".join(cur["lines"]))
            warnings.extend(warns)
            for ref in refs:
                problem = check_ref(ref)
                if problem:
                    warnings.append("check %s - %s" % (ref, problem))
            if refs:
                groups.append({"heading": cur["heading"], "refs": refs})
        elif cur["heading"]:
            warnings.append("no references under '%s'" % cur["heading"])
        cur = None

    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            # a blank line closes a group, unless it only has a heading so far
            if cur is not None and cur["lines"]:
                flush()
            continue
        heading, rest = split_heading(line)
        if heading:
            flush()
            cur = {"heading": heading, "lines": []}
            if rest:
                cur["lines"].append(rest)
            continue
        if cur is None:
            cur = {"heading": "", "lines": []}
        cur["lines"].append(rest)
    flush()

    total = sum(estimate(r) for g in groups for r in g["refs"])
    if total > MAX_VERSE_ESTIMATE:
        warnings.append("that is about %d verses, over the limit of %d per request"
                        % (total, MAX_VERSE_ESTIMATE))
    return groups, warnings
