# Verse cards on the Raspberry Pi

A small web app on top of `verses.py`. Open it from your iPhone over the tailnet, take a photo of an outline
(or paste references), check the list, and get dark verse cards you can save straight to Photos.

The app listens on `127.0.0.1` only. Tailscale Serve puts HTTPS in front of it, which the iPhone share sheet
needs for "Save to Photos". The API login stays in `.env` on the Pi and never reaches the browser. Photos are
read in memory and not kept.

## 1. Install

```
sudo apt update
sudo apt install -y tesseract-ocr python3-venv git
git clone https://github.com/aaronluebbert/verse-fetcher.git ~/verse-fetcher
cd ~/verse-fetcher
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. API login

```
cp .env.example .env
nano .env        # fill in LSM_APPID and LSM_TOKEN
```

`.env` is gitignored. Do not paste the token anywhere else.

## 3. Try it

```
.venv/bin/python -m unittest discover -s tests
.venv/bin/python app.py
curl localhost:8000/healthz      # from a second terminal, expect "login":true and "tesseract":true
```

Stop it with Ctrl+C once that works.

## 4. Run it on boot

Open `deploy/verse-web.service` and change `User` and the paths if yours are not `pi` and
`/home/pi/verse-fetcher`. Then:

```
sudo cp deploy/verse-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now verse-web
journalctl -u verse-web -f       # logs
```

## 5. HTTPS over the tailnet

In the Tailscale admin console, under DNS, make sure MagicDNS and HTTPS Certificates are both on. Then on the Pi:

```
sudo tailscale serve --bg 8000
tailscale serve status           # shows the https://<pi-name>.<tailnet>.ts.net address
```

Open that address in Safari on the iPhone. Share, then Add to Home Screen, gives it an app icon.

## Using it

1. Choose or take a photo. Drag over just the lines of references, then tap Read. Reading only the selected part
   is faster and more accurate.
2. The list lands in the References box. Anything the reader was unsure of is listed above it. Compare against
   your photo, fix the box, or paste a list in instead.
3. Tap Get verses, then Save to Photos. The share sheet opens with all the cards, choose Save Images.

Format of the box: a heading, a colon, then references. A blank line starts a new group, and each group starts a
new card.

```
The sense of death: John 4:13; Isa. 55:2; Luke 15:14-16
Rom. 7:18-19; John 15:5

The sense of life: John 6:35; Phil. 4:11-12
```

If a photo is too hard for the reader (curved page, glare, handwriting), send it to Claude, ask for the
references as a list, and paste the result into the box.

## Updating

```
cd ~/verse-fetcher && git pull && sudo systemctl restart verse-web
```

## Good to know

- Anyone who can reach the Pi on your tailnet can use the page and spend your API calls. Limit it with
  Tailscale ACLs if the tailnet is shared.
- Reading a photo takes roughly 10 to 20 seconds on a Pi 5.
- 401 or 403 from the API means the app id and token in `.env` do not match what you entered on api.lsm.org.
