# tests for the web routes with the lsm api faked out
#   python3 -m unittest discover -s tests

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["LSM_APPID"] = "test-app"
os.environ["LSM_TOKEN"] = "test-token"

import app as webapp
import verses


def fake_api(refs, creds, baseurl):
    # one fake verse per requested reference, text is obviously not scripture
    return {
        "verses": [{"ref": r, "text": "fake text for " + r, "urlpfx": ""} for r in refs],
        "message": "",
        "copyright": "fake copyright line",
    }


class Routes(unittest.TestCase):
    def setUp(self):
        self.real = verses.call_api
        verses.call_api = fake_api
        webapp.cache.clear()
        self.client = webapp.app.test_client()

    def tearDown(self):
        verses.call_api = self.real

    def test_check(self):
        r = self.client.post("/api/check", json={"text": "Life: John 6:35; Rom. 8:2\n\nDeath: John 4:13"})
        data = r.get_json()
        self.assertEqual(data["count"], 3)
        self.assertEqual([g["heading"] for g in data["groups"]], ["Life", "Death"])

    def test_fetch_keeps_groups_and_order(self):
        r = self.client.post("/api/fetch", json={"text": "Life: John 6:35; Rom. 8:2\n\nDeath: John 4:13"})
        data = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(data["copyright"], "fake copyright line")
        self.assertEqual(data["groups"][0]["heading"], "Life")
        self.assertEqual([v["ref"] for v in data["groups"][0]["verses"]], ["John 6:35", "Rom. 8:2"])
        self.assertEqual(data["groups"][1]["verses"][0]["ref"], "John 4:13")

    def test_fetch_uses_cache(self):
        calls = []

        def counting(refs, creds, baseurl):
            calls.append(refs)
            return fake_api(refs, creds, baseurl)

        verses.call_api = counting
        for _ in range(2):
            self.client.post("/api/fetch", json={"text": "John 3:16"})
        self.assertEqual(len(calls), 1)

    def test_fetch_rejects_empty_and_huge(self):
        self.assertEqual(self.client.post("/api/fetch", json={"text": "nothing here"}).status_code, 400)
        big = "; ".join("Psa. %d" % n for n in range(1, 30))
        self.assertEqual(self.client.post("/api/fetch", json={"text": big}).status_code, 400)

    def test_api_error_becomes_502(self):
        def broken(refs, creds, baseurl):
            raise verses.ApiError("the api rejected the login (http 401)")

        verses.call_api = broken
        r = self.client.post("/api/fetch", json={"text": "John 3:16"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("rejected the login", r.get_json()["error"])

    def test_missing_login(self):
        keep = os.environ.pop("LSM_TOKEN")
        try:
            r = self.client.post("/api/fetch", json={"text": "John 3:16"})
            self.assertEqual(r.status_code, 500)
            self.assertNotIn("test-app", r.get_data(as_text=True))
        finally:
            os.environ["LSM_TOKEN"] = keep

    def test_ocr_needs_a_photo(self):
        self.assertEqual(self.client.post("/api/ocr").status_code, 400)

    def test_pages_are_served(self):
        self.assertEqual(self.client.get("/healthz").get_json()["ok"], True)
        self.assertEqual(self.client.get("/api/../app.py").status_code, 404)


if __name__ == "__main__":
    unittest.main()
