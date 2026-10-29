#!/usr/bin/env python3
# recovery version verse fetcher
# takes a messy list of verse references and pulls the text from the lsm recovery version api
#
# setup - put LSM_APPID and LSM_TOKEN in a .env file next to this script (see .env.example)
#         real environment variables also work and win over the file
#         LSM_BASEURL is only needed if it is not the default
# usage - python3 verses.py "Rom 6:3-5, John 15:1, 4-5"
#         python3 verses.py --file refs.txt
#         cat refs.txt | python3 verses.py --json
#         python3 verses.py --check "John 3:16"   (shows the cleaned request, makes no network call)
#
# only the python standard library is used, nothing to install

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_BASEURL = "https://api.lsm.org/recver"
API_CAP = 50        # max verses the api returns per request
BATCH_BUDGET = 40   # estimated verses per request, leaves headroom under the cap
LINK_PREFIX = "https://text.recoveryversion.bible/"

# -- book table (full name, recommended abbreviation) --------------------------

BOOKS = [
    ("Genesis", "Gen."), ("Exodus", "Exo."), ("Leviticus", "Lev."), ("Numbers", "Num."),
    ("Deuteronomy", "Deut."), ("Joshua", "Josh."), ("Judges", "Judg."), ("Ruth", "Ruth"),
    ("1 Samuel", "1 Sam."), ("2 Samuel", "2 Sam."), ("1 Kings", "1 Kings"), ("2 Kings", "2 Kings"),
    ("1 Chronicles", "1 Chron."), ("2 Chronicles", "2 Chron."), ("Ezra", "Ezra"),
    ("Nehemiah", "Neh."), ("Esther", "Esth."), ("Job", "Job"), ("Psalms", "Psa."),
    ("Proverbs", "Prov."), ("Ecclesiastes", "Eccl."), ("Song of Solomon", "S.S."),
    ("Isaiah", "Isa."), ("Jeremiah", "Jer."), ("Lamentations", "Lam."), ("Ezekiel", "Ezek."),
    ("Daniel", "Dan."), ("Hosea", "Hosea"), ("Joel", "Joel"), ("Amos", "Amos"),
    ("Obadiah", "Oba."), ("Jonah", "Jonah"), ("Micah", "Micah"), ("Nahum", "Nahum"),
    ("Habakkuk", "Hab."), ("Zephaniah", "Zeph."), ("Haggai", "Hag."), ("Zechariah", "Zech."),
    ("Malachi", "Mal."), ("Matthew", "Matt."), ("Mark", "Mark"), ("Luke", "Luke"),
    ("John", "John"), ("Acts", "Acts"), ("Romans", "Rom."), ("1 Corinthians", "1 Cor."),
    ("2 Corinthians", "2 Cor."), ("Galatians", "Gal."), ("Ephesians", "Eph."),
    ("Philippians", "Phil."), ("Colossians", "Col."), ("1 Thessalonians", "1 Thes."),
    ("2 Thessalonians", "2 Thes."), ("1 Timothy", "1 Tim."), ("2 Timothy", "2 Tim."),
    ("Titus", "Titus"), ("Philemon", "Philem."), ("Hebrews", "Heb."), ("James", "James"),
    ("1 Peter", "1 Pet."), ("2 Peter", "2 Pet."), ("1 John", "1 John"), ("2 John", "2 John"),
    ("3 John", "3 John"), ("Jude", "Jude"), ("Revelation", "Rev."),
]

# the api wants these cited by verse only, like Jude 9 and never Jude 1:9
SINGLE = {"Oba.", "Philem.", "2 John", "3 John", "Jude"}

# common spellings that are not the full name or the recommended abbreviation
ALIASES = {
    "ps": "Psa.", "psalm": "Psa.", "psm": "Psa.", "ex": "Exo.", "exod": "Exo.",
    "dt": "Deut.", "songofsongs": "S.S.", "songs": "S.S.", "song": "S.S.", "ss": "S.S.",
    "sos": "S.S.", "mt": "Matt.", "mk": "Mark", "lk": "Luke", "jn": "John", "php": "Phil.",
    "1thess": "1 Thes.", "2thess": "2 Thes.", "1kgs": "1 Kings", "2kgs": "2 Kings",
    "revelations": "Rev.", "proverb": "Prov.",
}


def load_dotenv():
    # reads KEY=VALUE lines from a .env next to this script or in the current folder
    # values already set in the real environment are never overwritten
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.getcwd()):
        path = os.path.join(folder, ".env")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                if key and val and not os.environ.get(key):
                    os.environ[key] = val


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


LOOKUP = {}
for _full, _abbr in BOOKS:
    LOOKUP[norm(_full)] = _abbr
    LOOKUP[norm(_abbr)] = _abbr
LOOKUP.update(ALIASES)


def lookup_book(raw):
    raw = raw.strip(" .")
    # roman numeral prefixes like "I John" become "1 John"
    raw = re.sub(r"^(iii|ii|i)\s+(?=[A-Za-z])", lambda m: str(len(m.group(1))) + " ", raw, flags=re.I)
    key = norm(raw)
    if key in LOOKUP:
        return LOOKUP[key]
    # unique prefix of a full name is fine ("Roman" -> Romans) but ambiguous ones are not
    if len(key) >= 3:
        hits = {abbr for full, abbr in BOOKS if norm(full).startswith(key)}
        if len(hits) == 1:
            return hits.pop()
    return None


# -- cleaning and parsing ------------------------------------------------------

def clean_text(text):
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    text = text.replace("\r", "\n")
    # list numbering and bullets at the start of a line
    text = re.sub(r"(?m)^\s*(?:\d{1,3}[.)]\s+|[-*\u2022]+\s*)", "", text)
    # 3.16 is a common transcription of 3:16
    text = re.sub(r"(\d)\.(\d)", r"\1:\2", text)
    # "and" and "&" between references act like a comma
    text = re.sub(r"(?i)\s+(?:and|&)\s+", ", ", text)
    # the api only accepts letters, digits, period, hyphen, comma, colon, semicolon, space
    text = re.sub(r"[^A-Za-z0-9.\-,:;\n ]", " ", text)
    # a line that ends in a separator continues on the next line, otherwise a newline is a semicolon
    text = re.sub(r"([,;])[ ]*\n+[ ]*", r"\1 ", text)
    text = re.sub(r"[ ]*\n+[ ]*", ";", text)
    text = re.sub(r"[ ]+", " ", text)
    return text.strip(" ;,")


def parse_refs(text):
    # returns (list of full references that each carry their own book, list of warnings)
    refs = []
    warnings = []
    book = None
    chapter = None
    parts = re.split(r"([;,])", clean_text(text))
    for i in range(0, len(parts), 2):
        tok = parts[i].strip()
        sep = parts[i - 1] if i > 0 else ";"
        if not tok:
            continue
        # drop verse part letters and "ff" like 45b
        tok = re.sub(r"(?<=\d)(?:ff|f|[a-d])(?![A-Za-z])", "", tok)
        m = re.match(r"^((?:[1-3]\s*)?[A-Za-z][A-Za-z.\s]*?)\s*(\d[\d\s:\-]*)?$", tok)
        has_book = bool(m)
        if m:
            name = lookup_book(m.group(1))
            if name is None:
                warnings.append("unknown book name: " + m.group(1).strip())
                book, chapter = None, None
                continue
            book, chapter = name, None
            rest = (m.group(2) or "").strip()
        else:
            rest = tok
            if not re.fullmatch(r"[\d\s:\-]+", rest):
                warnings.append("could not read: " + tok)
                continue
            if book is None:
                warnings.append("no book for: " + tok)
                continue

        rest = re.sub(r"\s*([:\-])\s*", r"\1", rest).strip("-: ")
        if re.fullmatch(r"\d+ \d+", rest):
            warnings.append("read '%s %s' as chapter:verse" % (book, rest))
            rest = rest.replace(" ", ":")
        rest = rest.replace(" ", "")
        if not rest:
            if has_book:
                warnings.append("no chapter or verse after " + book)
            continue

        if book in SINGLE and rest.startswith("1:"):
            rest = rest[2:]

        if ":" in rest:
            chapter = int(rest.split(":")[0])
            refs.append("%s %s" % (book, rest))
        elif book in SINGLE:
            refs.append("%s %s" % (book, rest))
        elif not has_book and sep == "," and chapter is not None:
            # a comma continues the verse list of the current chapter
            refs.append("%s %d:%s" % (book, chapter, rest))
        else:
            # whole chapter or chapter range
            refs.append("%s %s" % (book, rest))
    return split_big_ranges(refs), warnings


def split_big_ranges(refs):
    out = []
    for ref in refs:
        m = re.fullmatch(r"(.+ \d+):(\d+)-(\d+)", ref)
        if m and int(m.group(3)) - int(m.group(2)) + 1 > API_CAP:
            a, b = int(m.group(2)), int(m.group(3))
            while a <= b:
                e = min(a + API_CAP - 1, b)
                out.append("%s:%d-%d" % (m.group(1), a, e))
                a = e + 1
        else:
            out.append(ref)
    return out


def estimate(ref):
    m = re.fullmatch(r"(.+) \d+:(\d+)-(\d+)", ref)
    if m:
        return max(1, int(m.group(3)) - int(m.group(2)) + 1)
    if re.fullmatch(r".+ \d+:\d+", ref):
        return 1
    m = re.fullmatch(r"(.+) (\d+)-(\d+)", ref)
    if m and m.group(1) in SINGLE:
        return max(1, int(m.group(3)) - int(m.group(2)) + 1)
    m = re.fullmatch(r"(.+) \d+", ref)
    if m and m.group(1) in SINGLE:
        return 1
    # whole chapter or cross chapter range, size unknown
    return BATCH_BUDGET


def make_batches(refs):
    batches, cur, total = [], [], 0
    for ref in refs:
        n = estimate(ref)
        if cur and total + n > BATCH_BUDGET:
            batches.append(cur)
            cur, total = [], 0
        cur.append(ref)
        total += n
    if cur:
        batches.append(cur)
    return batches


# -- api calls -----------------------------------------------------------------

class ApiError(Exception):
    pass


def call_api(refs, creds, baseurl):
    appid, token = creds
    query = urllib.parse.urlencode({"String": "; ".join(refs)})
    url = baseurl.rstrip("/") + "/txo.php?" + query
    req = urllib.request.Request(url)
    auth = base64.b64encode((appid + ":" + token).encode("utf-8")).decode("ascii")
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("Accept", "application/xml")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ApiError("the api rejected the login (http %d) - check LSM_APPID and LSM_TOKEN "
                           "in .env, the app id must match what you entered on api.lsm.org" % e.code)
        raise ApiError("http error %d from the api" % e.code)
    except urllib.error.URLError as e:
        raise ApiError("could not reach the api: %s" % e.reason)
    # xml is the default output and is documented as well formed, the json examples are not
    # always valid json when a verse contains quote marks
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ApiError("the api did not return valid xml, first bytes: "
                       + body[:200].decode("utf-8", "replace"))
    verses = []
    for v in root.iter("verse"):
        verses.append({
            "ref": (v.findtext("ref") or "").strip(),
            "text": (v.findtext("text") or "").strip(),
            "urlpfx": (v.findtext("urlpfx") or "").strip(),
        })
    return {
        "verses": verses,
        "message": (root.findtext("message") or "").strip(),
        "copyright": (root.findtext("copyright") or "").strip(),
    }


class Fetcher:
    def __init__(self, creds, baseurl):
        self.creds = creds
        self.baseurl = baseurl
        self.verses = []
        self.warnings = []
        self.copyright = ""

    def call(self, refs):
        return call_api(refs, self.creds, self.baseurl)

    def take(self, resp):
        self.verses.extend(resp["verses"])
        if resp["copyright"]:
            self.copyright = resp["copyright"]

    def fetch(self, refs):
        resp = self.call(refs)
        if "exceeded" in resp["message"] and str(API_CAP) in resp["message"]:
            if len(refs) > 1:
                # too many verses for one request, split the batch and try each half
                mid = len(refs) // 2
                self.fetch(refs[:mid])
                self.fetch(refs[mid:])
            else:
                self.fetch_long(refs[0], resp)
            return
        self.take(resp)
        if resp["message"]:
            self.warnings.append(resp["message"])

    def fetch_long(self, ref, first):
        # a single reference with more than 50 verses, like a whole chapter of Psalm 119
        self.take(first)
        m = re.fullmatch(r"(.+) (\d+)", ref)
        if not m:
            self.warnings.append("only the first %d verses of %s were returned" % (API_CAP, ref))
            return
        head = "%s %s" % (m.group(1), m.group(2))
        t = re.search(r"requested (\d+) verses", first["message"])
        total = int(t.group(1)) if t else None
        start = API_CAP + 1
        while total is None or start <= total:
            end = start + API_CAP - 1
            if total is not None:
                end = min(end, total)
            resp = self.call(["%s:%d-%d" % (head, start, end)])
            if not resp["verses"]:
                break
            self.take(resp)
            if len(resp["verses"]) < API_CAP:
                break
            start = end + 1


# -- output --------------------------------------------------------------------

def render_text(fetcher):
    lines = []
    for v in fetcher.verses:
        lines.append("%s - %s" % (v["ref"], v["text"]))
        lines.append("")
    if fetcher.copyright:
        lines.append(fetcher.copyright)
    return "\n".join(lines).rstrip() + "\n"


def render_json(fetcher):
    verses = []
    for v in fetcher.verses:
        link = LINK_PREFIX + v["urlpfx"] if v["urlpfx"] else ""
        verses.append({"ref": v["ref"], "text": v["text"], "link": link})
    return json.dumps({"verses": verses, "warnings": fetcher.warnings,
                       "copyright": fetcher.copyright}, indent=2, ensure_ascii=False) + "\n"


def read_input(args):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    if args.refs:
        return "\n".join(args.refs)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="fetch recovery version verses from a list of references")
    ap.add_argument("refs", nargs="*", help="verse references, or use --file or stdin")
    ap.add_argument("-f", "--file", help="read the reference list from a text file")
    ap.add_argument("--json", action="store_true", help="print json instead of plain text")
    ap.add_argument("--check", action="store_true", help="show the cleaned request and stop, no network")
    ap.add_argument("--baseurl", default=os.environ.get("LSM_BASEURL", DEFAULT_BASEURL))
    args = ap.parse_args(argv)

    text = read_input(args)
    refs, warnings = parse_refs(text)
    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    if not refs:
        print("no verse references found", file=sys.stderr)
        return 1
    batches = make_batches(refs)

    if args.check:
        print("references (%d):" % len(refs))
        for r in refs:
            print("  " + r)
        print("requests (%d):" % len(batches))
        for i, b in enumerate(batches, 1):
            print("  %d: %s" % (i, "; ".join(b)))
        return 0

    load_dotenv()
    appid = os.environ.get("LSM_APPID", "")
    token = os.environ.get("LSM_TOKEN", "")
    if not appid or not token:
        print("LSM_APPID or LSM_TOKEN is missing - fill them in the .env file next to verses.py",
              file=sys.stderr)
        return 2

    fetcher = Fetcher((appid, token), args.baseurl)
    code = 0
    try:
        for b in batches:
            fetcher.fetch(b)
    except ApiError as e:
        print("error: %s" % e, file=sys.stderr)
        code = 1
    for w in fetcher.warnings:
        print("api message: " + w, file=sys.stderr)
    if fetcher.verses:
        sys.stdout.write(render_json(fetcher) if args.json else render_text(fetcher))
    return code


if __name__ == "__main__":
    sys.exit(main())
