# verse-fetcher

Fetches Recovery Version verses from the LSM API for a list of references, in the order you give them.
Personal use only.

## setup

1. copy `.env.example` to `.env`
2. fill in `LSM_APPID` (the identifier from your api.lsm.org app) and `LSM_TOKEN`
3. `.env` is gitignored, so the token stays off GitHub

Needs Python 3.8+ and nothing else (standard library only).

## use

```
python verses.py "Rom 6:3-5, John 15:1, 4-5; Acts 9:22; 26:19"
python verses.py --file refs.txt
python verses.py --json "John 1:14"
python verses.py --check "Eph 4.4-6 and Jude 1:9"
```

`--check` prints the cleaned request without calling the API.

It cleans up bullets, numbering, en dashes, `3.16` style references, verse letters (`15b`), "and" between
references and book names without periods. It stays under the API cap of 50 verses per request by batching,
and splits long chapters automatically.

Verses accessed from the Holy Bible Recovery Version (text-only edition), Living Stream Ministry, www.lsm.org
