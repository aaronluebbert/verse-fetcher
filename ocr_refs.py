# reads verse references out of a photo with tesseract
# pillow does the image cleanup, tesseract is called as a subprocess, nothing else is needed
#
# the result is plain text in the same group format the web page accepts, so a person can
# fix it by hand before anything is fetched. references the reader was not sure about are
# listed separately so the page can ask for a double check.

import csv
import io
import os
import re
import subprocess
import tempfile
from difflib import get_close_matches

from PIL import Image, ImageChops, ImageFilter, ImageOps

from refparse import CHAPTERS, check_ref
from verses import LOOKUP, SINGLE, lookup_book, norm

TARGET_EDGE = 2800      # long edge in pixels the photo is scaled to before reading
MIN_WORD_CONF = 30      # words below this at the start of a line are margin noise
SURE_CONF = 75          # a reference with any word under this gets flagged
FUZZY_CUTOFF = 0.66
MAX_PIXELS = 60_000_000

# tesseract page segmentation modes to try, sparse text first since photos of notes are messy
# the last one only runs when the first two found very little
RUNS = [11, 4, 6]
GOOD_ENOUGH = 12        # references found that skip the extra run
MIN_HEADING_CONF = 70   # average confidence needed for words to count as a heading


class OcrError(Exception):
    pass


# -- image cleanup -------------------------------------------------------------

def prepare(img):
    img = ImageOps.exif_transpose(img).convert("L")
    w, h = img.size
    scale = TARGET_EDGE / max(w, h)
    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    # flatten shadows and glare: subtract a heavy blur so only the ink is left
    blur = img.filter(ImageFilter.GaussianBlur(35))
    ink = ImageChops.subtract(blur, img).point(lambda v: min(255, v * 4))
    return ImageOps.autocontrast(ImageOps.invert(ink), cutoff=1)


def run_tesseract(img, psm):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "in.png")
        img.save(path)
        cmd = ["tesseract", path, "stdout", "--psm", str(psm), "-l", "eng", "tsv"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise OcrError("tesseract is not installed - run: sudo apt install tesseract-ocr")
        except subprocess.TimeoutExpired:
            raise OcrError("reading the photo took too long")
    if res.returncode != 0:
        raise OcrError("tesseract failed: " + res.stderr.strip()[:200])
    return res.stdout


def tsv_lines(tsv):
    # returns a list of lines, each a list of (text, confidence)
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines = {}
    order = []
    for r in rows:
        if r.get("level") != "5":
            continue
        text = (r.get("text") or "").strip()
        if not text:
            continue
        key = (r["block_num"], r["par_num"], r["line_num"])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append((text, int(float(r["conf"]))))
    return [lines[k] for k in order]


# -- turning ocr words into references ----------------------------------------

DIGITISH = re.compile(r"[0-9lIOoSB|:;,.\-]+")
SEP_BETWEEN = re.compile("(?<=\\d)[\\u00b0'`\\u00b4\\u2018\\u2019\\u00b7\\u2022/!.](?=\\d)")
REF = re.compile(r"(?<![A-Za-z0-9])((?:[123]\s?|[Il|]\s)?[A-Za-z]{2,12})\.?\s*(\d{1,4})"
                 r"(?:\s*:\s*(\d{1,3}))?(?:\s*-\s*(\d{1,3}))?")
LOOKALIKE = str.maketrans({"l": "1", "I": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"})
LOOKUP_KEYS = sorted(LOOKUP)

# letters ocr mixes up with a capital i, used to rescue names like "tsa" for Isa.
CONFUSE = str.maketrans("tl1|", "iiii")
CONFUSED_KEYS = {}
for _key in LOOKUP_KEYS:
    CONFUSED_KEYS.setdefault(_key.translate(CONFUSE), set()).add(LOOKUP[_key])


def fix_word(w):
    w = w.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    if re.search(r"\d", w):
        w = SEP_BETWEEN.sub(":", w)
        if DIGITISH.fullmatch(w):
            w = w.translate(LOOKALIKE)
    return w


def resolve_book(raw):
    # returns (recommended abbreviation, was_fuzzy) or (None, False)
    raw = re.sub(r"^[l|]\s", "1 ", raw.strip())
    name = lookup_book(raw)
    if name:
        return name, False
    key = norm(raw)
    letters = re.sub(r"[^a-z]", "", key)
    if len(letters) < 3:
        return None, False
    same = CONFUSED_KEYS.get(key.translate(CONFUSE))
    if same and len(same) == 1:
        return next(iter(same)), True
    pool = [k for k in LOOKUP_KEYS if k[:1].isdigit() == key[:1].isdigit()
            and (not key[:1].isdigit() or k[:1] == key[:1])]
    hit = get_close_matches(key, pool, n=1, cutoff=FUZZY_CUTOFF)
    if hit:
        return LOOKUP[hit[0]], True
    return None, False


def split_glued(book, digits):
    # a dropped colon glues chapter and verse together ("4030" for 40:30)
    # only repair when exactly one split gives a believable chapter and verse
    limit = CHAPTERS.get(book, 150)
    options = []
    for k in range(1, len(digits)):
        ch, vs = digits[:k], digits[k:]
        if vs.startswith("0") or ch.startswith("0"):
            continue
        if 1 <= int(ch) <= limit and 1 <= int(vs) <= 176:
            options.append((int(ch), int(vs)))
    return options[0] if len(options) == 1 else None


def refish(words):
    # a line of references is mostly numbers and book names, prose is not
    if not words:
        return False
    hits = 0
    for text, _ in words:
        bare = text.strip(".,;:")
        if re.search(r"\d", text) or bare == "" or bare == "-":
            hits += 1
        elif len(bare) > 2 and lookup_book(bare):
            hits += 1
    return hits / len(words) >= 0.4


def pull_refs(words):
    # words is a list of (text, conf), returns (refs, uncertain) where uncertain is
    # a list of (ref, reason)
    texts = [fix_word(t) for t, _ in words]
    spans = []
    pos = 0
    for t, (_, conf) in zip(texts, words):
        spans.append((pos, pos + len(t), conf))
        pos += len(t) + 1
    joined = " ".join(texts)

    refs, uncertain = [], []
    for m in REF.finditer(joined):
        book, fuzzy = resolve_book(m.group(1))
        if not book:
            continue
        digits, v1, v2 = m.group(2), m.group(3), m.group(4)
        reasons = []
        if fuzzy:
            reasons.append("book name was a guess")
        if book in SINGLE:
            ref = "%s %s" % (book, v1 or digits)
        else:
            limit = CHAPTERS.get(book, 150)
            if v1 is None and int(digits) > limit:
                fix = split_glued(book, digits)
                if fix:
                    digits, v1 = str(fix[0]), str(fix[1])
                    reasons.append("colon was missing")
            if v1 is None and v2 is not None:
                reasons.append("chapter and verse were unclear")
            elif v1 is None:
                reasons.append("no verse number, this would fetch the whole chapter")
            ref = "%s %s" % (book, digits)
            if v1:
                ref += ":" + v1
            if v2:
                ref += "-" + v2
        low = min([c for s, e, c in spans if s < m.end() and e > m.start()] or [100])
        if low < SURE_CONF:
            reasons.append("low confidence")
        problem = check_ref(ref)
        if problem:
            reasons.append(problem)
        refs.append(ref)
        if reasons:
            uncertain.append((ref, ", ".join(reasons)))
    return refs, uncertain


def find_heading(words):
    # a heading is two to six plain words ending in a colon, like "The sense of death:"
    for j, (text, _) in enumerate(words[:8]):
        if re.search(r"\d", text):
            return None
        if text.endswith(":") or text.endswith(";"):
            if j < 1:
                return None
            if sum(c for _, c in words[:j + 1]) / (j + 1) < MIN_HEADING_CONF:
                return None
            label = " ".join(t for t, _ in words[:j + 1]).rstrip(":;").strip()
            label = re.sub(r"^[^A-Za-z]+", "", label)
            if len(label.split()) < 2 or lookup_book(label):
                return None
            return label[:1].upper() + label[1:], j + 1
    return None


def drop_noise(words):
    # margin scribbles come out as low confidence words at the start of a line
    i = 0
    while i < len(words) and words[i][1] < MIN_WORD_CONF:
        i += 1
    return words[i:]


def build(lines):
    # returns (groups, uncertain) where groups is a list of {"heading", "refs"}
    groups = []
    uncertain = []
    cur = None
    pending = []

    def close():
        nonlocal cur, pending
        if cur is not None and pending:
            refs, unsure = pull_refs(pending)
            cur["refs"].extend(refs)
            uncertain.extend(unsure)
        pending = []

    for words in lines:
        words = drop_noise(words)
        if not words:
            continue
        head = find_heading(words)
        if head:
            close()
            cur = {"heading": head[0], "refs": []}
            groups.append(cur)
            words = words[head[1]:]
        if not refish(words):
            continue
        if cur is None:
            cur = {"heading": "", "refs": []}
            groups.append(cur)
        pending.extend(words)
    close()
    return [g for g in groups if g["refs"]], uncertain


def to_text(groups):
    blocks = []
    for g in groups:
        body = "; ".join(g["refs"])
        blocks.append(("%s: %s" % (g["heading"], body)) if g["heading"] else body)
    return "\n\n".join(blocks)


def score(groups, uncertain):
    refs = sum(len(g["refs"]) for g in groups)
    problems = sum(1 for _, why in uncertain if "chapters" in why or "backwards" in why)
    headings = sum(1 for g in groups if g["heading"])
    return refs - problems + 2 * headings


# -- entry point ---------------------------------------------------------------

def read_image(data):
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise OcrError("that photo is too large, try a smaller one")
        img.load()
    except OcrError:
        raise
    except Exception:
        raise OcrError("that file could not be read as an image")

    flat = prepare(img)
    best = None
    for n, psm in enumerate(RUNS):
        if n >= 2 and best and sum(len(g["refs"]) for g in best[1]) >= GOOD_ENOUGH:
            break
        lines = tsv_lines(run_tesseract(flat, psm))
        groups, unsure = build(lines)
        s = score(groups, unsure)
        if best is None or s > best[0]:
            best = (s, groups, unsure, "psm %d" % psm)
    _, groups, unsure, label = best
    return {
        "text": to_text(groups),
        "uncertain": [{"ref": r, "why": w} for r, w in unsure],
        "count": sum(len(g["refs"]) for g in groups),
        "variant": label,
    }
