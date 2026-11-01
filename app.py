#!/usr/bin/env python3
# small web front end for verses.py
# upload a photo or paste references, get dark verse cards back
#
# run it behind tailscale serve, it only listens on localhost
#   gunicorn -w 1 --threads 4 -t 150 -b 127.0.0.1:8000 app:app
#   python3 app.py                      (development server)
#
# the api login stays on this machine in .env, the browser never sees it

import os
import shutil
import threading
from collections import OrderedDict

from flask import Flask, jsonify, request, send_from_directory

import verses
from ocr_refs import OcrError, read_image
from refparse import MAX_VERSE_ESTIMATE, parse_groups

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.json.sort_keys = False

verses.load_dotenv()
BASEURL = os.environ.get("LSM_BASEURL", verses.DEFAULT_BASEURL)

# one photo at a time, tesseract is heavy on a pi
ocr_slot = threading.Semaphore(1)

# small in-memory cache so a retry or a second tap does not spend api calls
CACHE_MAX = 200
cache = OrderedDict()
cache_lock = threading.Lock()


def error(message, status=400):
    resp = jsonify({"error": message})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def estimate_total(groups):
    return sum(verses.estimate(r) for g in groups for r in g["refs"])


def fetch_refs(refs, creds):
    key = tuple(refs)
    with cache_lock:
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
            return hit
    fetcher = verses.Fetcher(creds, BASEURL)
    for batch in verses.make_batches(refs):
        fetcher.fetch(batch)
    result = {
        "verses": [{"ref": v["ref"], "text": v["text"]} for v in fetcher.verses],
        "copyright": fetcher.copyright,
        "warnings": list(fetcher.warnings),
    }
    if result["verses"]:
        with cache_lock:
            cache[key] = result
            while len(cache) > CACHE_MAX:
                cache.popitem(last=False)
    return result


def text_from_request():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    if not isinstance(text, str):
        return None
    return text[:20000]


@app.errorhandler(413)
def too_big(_):
    return error("that file is too large, try a smaller photo", 413)


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html", max_age=0)


@app.route("/static/<path:name>")
def static_file(name):
    return send_from_directory(STATIC, name, max_age=0)


@app.route("/healthz")
def health():
    load = bool(os.environ.get("LSM_APPID") and os.environ.get("LSM_TOKEN"))
    return jsonify({"ok": True, "login": load, "tesseract": shutil.which("tesseract") is not None})


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    upload = request.files.get("image")
    if upload is None:
        return error("no photo was sent")
    data = upload.read()
    if not data:
        return error("the photo was empty")
    if not ocr_slot.acquire(timeout=120):
        return error("still reading another photo, try again in a moment", 503)
    try:
        result = read_image(data)
    except OcrError as e:
        return error(str(e), 422)
    finally:
        ocr_slot.release()
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/check", methods=["POST"])
def api_check():
    text = text_from_request()
    if text is None:
        return error("send the references as text")
    groups, warnings = parse_groups(text)
    total = estimate_total(groups)
    if total > MAX_VERSE_ESTIMATE:
        warnings.append("that is about %d verses, the limit is %d per request" % (total, MAX_VERSE_ESTIMATE))
    resp = jsonify({
        "groups": groups,
        "count": sum(len(g["refs"]) for g in groups),
        "warnings": warnings,
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    text = text_from_request()
    if text is None:
        return error("send the references as text")
    groups, warnings = parse_groups(text)
    if not groups:
        return error("no verse references found")
    if estimate_total(groups) > MAX_VERSE_ESTIMATE:
        return error("that is too many verses for one request, split it into smaller lists")

    creds = (os.environ.get("LSM_APPID", ""), os.environ.get("LSM_TOKEN", ""))
    if not all(creds):
        return error("LSM_APPID or LSM_TOKEN is missing - fill them in the .env file on the server", 500)

    out = []
    copyright_line = ""
    try:
        for g in groups:
            got = fetch_refs(g["refs"], creds)
            if not got["verses"]:
                warnings.append("no text came back for %s" % ", ".join(g["refs"][:3]))
            warnings.extend(got["warnings"])
            copyright_line = got["copyright"] or copyright_line
            out.append({"heading": g["heading"], "verses": got["verses"]})
    except verses.ApiError as e:
        return error(str(e), 502)
    resp = jsonify({"groups": out, "copyright": copyright_line, "warnings": warnings})
    resp.headers["Cache-Control"] = "no-store"
    return resp


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
