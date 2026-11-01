// verse cards - photo or pasted references in, dark cards out
// the cards are drawn here in the browser so the server only ever sends text

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    photo: $("photo"), cropper: $("cropper"), crop: $("crop"), read: $("read"), whole: $("whole"),
    ocrStatus: $("ocr-status"), doubts: $("doubts"), doubtsTitle: $("doubts-title"), doubtsList: $("doubts-list"),
    refs: $("refs"), paste: $("paste"), clear: $("clear"), checkStatus: $("check-status"),
    checkWarnings: $("check-warnings"), go: $("go"), fetchStatus: $("fetch-status"),
    results: $("results"), cards: $("cards"), save: $("save"), copy: $("copy"), saveNote: $("save-note")
  };

  function setStatus(el, message, kind) {
    el.textContent = message || "";
    if (kind) { el.setAttribute("data-kind", kind); } else { el.removeAttribute("data-kind"); }
  }

  function postJSON(url, body, signal) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: signal
    }).then(readReply);
  }

  function readReply(res) {
    return res.json().catch(function () { return {}; }).then(function (data) {
      if (!res.ok) { throw new Error(data.error || ("the server answered " + res.status)); }
      return data;
    });
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // -- photo and cropping ------------------------------------------------------

  var photo = null;      // ImageBitmap or img element
  var sel = null;        // selection in canvas pixels
  var drag = null;

  function dims(p) { return { w: p.naturalWidth || p.width, h: p.naturalHeight || p.height }; }

  function loadPhoto(file) {
    if (window.createImageBitmap) {
      return createImageBitmap(file, { imageOrientation: "from-image" }).catch(function () { return viaImage(file); });
    }
    return viaImage(file);
  }

  function viaImage(file) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = reject;
      img.src = URL.createObjectURL(file);
    });
  }

  els.photo.addEventListener("change", function () {
    var file = els.photo.files && els.photo.files[0];
    if (!file) { return; }
    loadPhoto(file).then(function (p) {
      photo = p;
      sel = null;
      els.cropper.hidden = false;
      drawCrop();
      updateReadButton();
      setStatus(els.ocrStatus, "");
      els.doubts.hidden = true;
    }).catch(function () {
      setStatus(els.ocrStatus, "That file could not be opened as a photo.", "error");
    });
  });

  function drawCrop() {
    if (!photo) { return; }
    var d = dims(photo);
    var c = els.crop;
    c.width = Math.min(1200, d.w);
    c.height = Math.round(c.width * d.h / d.w);
    var ctx = c.getContext("2d");
    ctx.drawImage(photo, 0, 0, c.width, c.height);
    if (sel) {
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(0, 0, c.width, sel.y);
      ctx.fillRect(0, sel.y + sel.h, c.width, c.height - sel.y - sel.h);
      ctx.fillRect(0, sel.y, sel.x, sel.h);
      ctx.fillRect(sel.x + sel.w, sel.y, c.width - sel.x - sel.w, sel.h);
      ctx.strokeStyle = "#d4ad72";
      ctx.lineWidth = Math.max(3, c.width / 300);
      ctx.strokeRect(sel.x, sel.y, sel.w, sel.h);
    }
  }

  function point(e) {
    var box = els.crop.getBoundingClientRect();
    var x = (e.clientX - box.left) * els.crop.width / box.width;
    var y = (e.clientY - box.top) * els.crop.height / box.height;
    return { x: Math.max(0, Math.min(els.crop.width, x)), y: Math.max(0, Math.min(els.crop.height, y)) };
  }

  els.crop.addEventListener("pointerdown", function (e) {
    if (!photo) { return; }
    els.crop.setPointerCapture(e.pointerId);
    drag = point(e);
    sel = { x: drag.x, y: drag.y, w: 0, h: 0 };
  });

  els.crop.addEventListener("pointermove", function (e) {
    if (!drag) { return; }
    var p = point(e);
    sel = { x: Math.min(drag.x, p.x), y: Math.min(drag.y, p.y), w: Math.abs(p.x - drag.x), h: Math.abs(p.y - drag.y) };
    drawCrop();
  });

  function endDrag() {
    if (!drag) { return; }
    drag = null;
    if (sel && (sel.w < 30 || sel.h < 20)) { sel = null; }
    drawCrop();
    updateReadButton();
  }
  els.crop.addEventListener("pointerup", endDrag);
  els.crop.addEventListener("pointercancel", endDrag);

  function updateReadButton() {
    els.read.textContent = sel ? "Read the selected part" : "Read the whole photo";
    els.whole.hidden = !sel;
  }

  els.whole.addEventListener("click", function () { sel = null; drawCrop(); updateReadButton(); });

  els.read.addEventListener("click", function () {
    if (!photo) { return; }
    var d = dims(photo);
    var k = d.w / els.crop.width;
    var s = sel ? { x: sel.x * k, y: sel.y * k, w: sel.w * k, h: sel.h * k } : { x: 0, y: 0, w: d.w, h: d.h };
    var scale = Math.min(1, 3000 / Math.max(s.w, s.h));
    var out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(s.w * scale));
    out.height = Math.max(1, Math.round(s.h * scale));
    out.getContext("2d").drawImage(photo, s.x, s.y, s.w, s.h, 0, 0, out.width, out.height);

    els.read.disabled = true;
    els.doubts.hidden = true;
    setStatus(els.ocrStatus, "Reading the photo. This can take 10 to 20 seconds on the Pi.");
    out.toBlob(function (blob) {
      var form = new FormData();
      form.append("image", blob, "photo.jpg");
      fetch("/api/ocr", { method: "POST", body: form }).then(readReply).then(showOcr).catch(function (err) {
        setStatus(els.ocrStatus, err.message, "error");
      }).then(function () { els.read.disabled = false; });
    }, "image/jpeg", 0.92);
  });

  function showOcr(data) {
    if (!data.count) {
      setStatus(els.ocrStatus, "No references found. Try selecting just the lines of references, or paste them below.", "error");
      return;
    }
    els.refs.value = data.text;
    setStatus(els.ocrStatus, "Found " + plural(data.count, "reference") +
      ". Check the list below against your photo before getting verses. Anything the reader missed is not listed.", "ok");
    els.doubtsList.textContent = "";
    (data.uncertain || []).forEach(function (u) {
      var li = document.createElement("li");
      var b = document.createElement("b");
      b.textContent = u.ref;
      var why = document.createElement("span");
      why.textContent = " - " + u.why;
      li.appendChild(b);
      li.appendChild(why);
      els.doubtsList.appendChild(li);
    });
    if (data.uncertain && data.uncertain.length) {
      els.doubtsTitle.textContent = plural(data.uncertain.length, "reference") + " worth a second look:";
      els.doubts.hidden = false;
    }
    runCheck();
    els.refs.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // -- reference box -------------------------------------------------------------

  var checkTimer = null;
  var checkCtl = null;

  els.refs.addEventListener("input", function () {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(runCheck, 300);
  });

  function runCheck() {
    var text = els.refs.value;
    els.checkWarnings.textContent = "";
    if (!text.trim()) {
      els.go.disabled = true;
      setStatus(els.checkStatus, "");
      return;
    }
    if (checkCtl) { checkCtl.abort(); }
    checkCtl = new AbortController();
    postJSON("/api/check", { text: text }, checkCtl.signal).then(function (d) {
      setStatus(els.checkStatus, plural(d.count, "reference") + " in " + plural(d.groups.length, "group"));
      d.warnings.slice(0, 8).forEach(function (w) {
        var li = document.createElement("li");
        li.textContent = w;
        els.checkWarnings.appendChild(li);
      });
      els.go.disabled = d.count === 0;
    }).catch(function (err) {
      if (err.name === "AbortError") { return; }
      setStatus(els.checkStatus, err.message, "error");
    });
  }

  els.clear.addEventListener("click", function () {
    els.refs.value = "";
    els.doubts.hidden = true;
    runCheck();
    els.refs.focus();
  });

  els.paste.addEventListener("click", function () {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      setStatus(els.checkStatus, "This browser will not paste from a button. Press and hold in the box and choose Paste.", "error");
      return;
    }
    navigator.clipboard.readText().then(function (t) {
      els.refs.value = t;
      runCheck();
    }).catch(function () {
      setStatus(els.checkStatus, "Paste was not allowed. Press and hold in the box and choose Paste.", "error");
    });
  });

  // -- getting verses ------------------------------------------------------------

  var current = null;   // { data, cards }

  els.go.addEventListener("click", function () {
    els.go.disabled = true;
    els.results.hidden = true;
    setStatus(els.fetchStatus, "Getting the verses.");
    postJSON("/api/fetch", { text: els.refs.value }).then(function (data) {
      var pages = paginate(data);
      return Promise.all(pages.map(function (p, i) { return renderPage(p, i, pages.length, data); })).then(function (cards) {
        showCards(data, cards);
        var warn = (data.warnings || []).slice(0, 4).join(". ");
        setStatus(els.fetchStatus, warn ? "Done, with notes: " + warn : "", warn ? "error" : "");
      });
    }).catch(function (err) {
      setStatus(els.fetchStatus, err.message, "error");
    }).then(function () { els.go.disabled = false; });
  });

  // -- card drawing --------------------------------------------------------------

  var CARD_W = 1080, CARD_H = 1350, PAD = 84;
  var SERIF = '"Iowan Old Style", "Palatino", "Palatino Linotype", "Book Antiqua", Georgia, serif';
  var C = { bg: "#14171c", text: "#ece7db", accent: "#d4ad72", muted: "#8d8b84" };
  var T = {
    head: { font: "600 62px " + SERIF, lh: 78, base: 58 },
    ref: { font: "italic 600 36px " + SERIF, lh: 50, base: 36 },
    body: { font: "400 42px " + SERIF, lh: 62, base: 42 },
    note: { font: "400 23px " + SERIF, lh: 32, base: 23 },
    index: { font: "400 28px " + SERIF }
  };

  var measure = document.createElement("canvas").getContext("2d");

  function wrap(text, font, width) {
    measure.font = font;
    var words = text.split(/\s+/).filter(Boolean);
    var lines = [];
    var line = "";
    words.forEach(function (w) {
      var t = line ? line + " " + w : w;
      if (line && measure.measureText(t).width > width) { lines.push(line); line = w; } else { line = t; }
    });
    if (line) { lines.push(line); }
    return lines;
  }

  function paginate(data) {
    var W = CARD_W - PAD * 2;
    var noteLines = data.copyright ? wrap(data.copyright, T.note.font, W - 170) : [];
    var foot = Math.max(70, noteLines.length * T.note.lh + 36);
    var limit = CARD_H - PAD - foot;
    var pages = [];
    var page = null, y = 0, top = 0;

    function open(heading, again) {
      page = { ops: [], rules: [], heading: heading, notes: noteLines };
      pages.push(page);
      y = PAD;
      if (heading) {
        wrap(again ? heading + ", continued" : heading, T.head.font, W).forEach(function (line) {
          page.ops.push({ font: T.head.font, color: C.text, text: line, y: y + T.head.base });
          y += T.head.lh;
        });
        y += 20;
        page.rules.push({ x: PAD, y: y, w: 96, h: 4 });
        y += 50;
      }
      top = y;
    }

    data.groups.forEach(function (g) {
      open(g.heading, false);
      g.verses.forEach(function (v) {
        var body = wrap(v.text, T.body.font, W);
        var i = 0, first = true;
        while (i < body.length) {
          // keep a verse whole when it fits on a fresh card, only split one that is taller than a card
          var whole = T.ref.lh + body.length * T.body.lh;
          var need = (i === 0 && whole <= limit - top) ? whole : T.ref.lh + Math.min(body.length - i, 2) * T.body.lh;
          if (y + need > limit && y > top) { open(g.heading, true); }
          page.ops.push({ font: T.ref.font, color: C.accent, text: first ? v.ref : v.ref + " (continued)", y: y + T.ref.base });
          y += T.ref.lh;
          first = false;
          while (i < body.length && y + T.body.lh <= limit) {
            page.ops.push({ font: T.body.font, color: C.text, text: body[i], y: y + T.body.base });
            y += T.body.lh;
            i++;
          }
          if (i < body.length) { open(g.heading, true); }
        }
        y += 34;
      });
    });
    return pages;
  }

  function renderPage(page, index, total, data) {
    var c = document.createElement("canvas");
    c.width = CARD_W;
    c.height = CARD_H;
    var ctx = c.getContext("2d");
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, CARD_W, CARD_H);
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = C.accent;
    page.rules.forEach(function (r) { ctx.fillRect(r.x, r.y, r.w, r.h); });
    page.ops.forEach(function (op) {
      ctx.font = op.font;
      ctx.fillStyle = op.color;
      ctx.fillText(op.text, PAD, op.y);
    });
    var baseline = CARD_H - PAD;
    if (total > 1) {
      ctx.font = T.index.font;
      ctx.fillStyle = C.muted;
      ctx.textAlign = "right";
      ctx.fillText((index + 1) + " of " + total, CARD_W - PAD, baseline);
      ctx.textAlign = "left";
    }
    if (index === total - 1 && page.notes.length) {
      ctx.font = T.note.font;
      ctx.fillStyle = C.muted;
      var n = page.notes.length;
      page.notes.forEach(function (line, k) {
        ctx.fillText(line, PAD, baseline - (n - 1 - k) * T.note.lh);
      });
    }
    return new Promise(function (resolve) {
      c.toBlob(function (blob) {
        var name = "verses-" + (index + 1) + ".png";
        resolve({
          blob: blob,
          url: URL.createObjectURL(blob),
          name: name,
          file: new File([blob], name, { type: "image/png" }),
          alt: (page.heading || "Verses") + ", card " + (index + 1) + " of " + total
        });
      }, "image/png");
    });
  }

  function showCards(data, cards) {
    if (current) { current.cards.forEach(function (c) { URL.revokeObjectURL(c.url); }); }
    current = { data: data, cards: cards };
    els.cards.textContent = "";
    cards.forEach(function (card) {
      var fig = document.createElement("figure");
      var img = document.createElement("img");
      img.src = card.url;
      img.alt = card.alt;
      var cap = document.createElement("figcaption");
      var a = document.createElement("a");
      a.href = card.url;
      a.download = card.name;
      a.textContent = "Download " + card.name;
      cap.appendChild(a);
      fig.appendChild(img);
      fig.appendChild(cap);
      els.cards.appendChild(fig);
    });
    setStatus(els.saveNote, "");
    els.results.hidden = false;
    els.results.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // -- saving and copying --------------------------------------------------------

  els.save.addEventListener("click", function () {
    if (!current) { return; }
    var files = current.cards.map(function (c) { return c.file; });
    if (navigator.canShare && navigator.canShare({ files: files })) {
      navigator.share({ files: files }).catch(function (err) {
        if (err && err.name !== "AbortError") { setStatus(els.saveNote, "Sharing failed: " + err.message, "error"); }
      });
    } else {
      setStatus(els.saveNote, "This browser cannot open the share sheet here. Press and hold a card and choose Save to Photos, or use the download links.", "error");
    }
  });

  els.copy.addEventListener("click", function () {
    if (!current) { return; }
    var d = current.data;
    var text = d.groups.map(function (g) {
      var body = g.verses.map(function (v) { return v.ref + " - " + v.text; }).join("\n\n");
      return (g.heading ? g.heading + "\n\n" : "") + body;
    }).join("\n\n\n");
    if (d.copyright) { text += "\n\n" + d.copyright; }
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      setStatus(els.saveNote, "Copying is not available in this browser.", "error");
      return;
    }
    navigator.clipboard.writeText(text).then(function () {
      setStatus(els.saveNote, "Copied.", "ok");
    }).catch(function () {
      setStatus(els.saveNote, "Copying was not allowed.", "error");
    });
  });

  runCheck();
})();
