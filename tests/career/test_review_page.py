import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

import core.dashboard as db
from core.career import pipeline, review_page, store, tracker


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(store, "DIR", tmp_path / "career")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(
        json.dumps({"dashboard_token": "secret123"}), encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), db._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _ready(n):
    ids = []
    for i in range(n):
        app = tracker.add({"url": f"https://wuzzuf.net/jobs/p/{i}", "title": f"Analyst {i}",
                           "company": "Valeo", "tier": "top", "score": 80 - i, "fit": "fits",
                           "missing": [], "channel": "wuzzuf", "status": "ready",
                           "draft": {"subject": "S", "body": "B"}})
        ids.append(app["id"])
    return ids


def _get(url, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5)


class TestPage:
    def test_is_served_with_its_placeholders_filled(self, server):
        with _get(f"{server}/jobs") as r:
            html = r.read().decode()
        assert "__TOKENS__" not in html and "__FONTS__" not in html
        assert "<title>Job Applications</title>" in html

    def test_every_colour_comes_from_the_tokens(self):
        style = review_page.PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
        assert re.findall(r"#[0-9A-Fa-f]{3,8}\b", style) == []
        used = set(re.findall(r"var\(--([a-z0-9-]+)\)", review_page.PAGE))
        defined = set(re.findall(r"--([a-z0-9-]+):", db._token_css()))
        assert used - defined == set()


class TestBatchApi:
    def test_the_drafts_need_the_token(self, server):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(f"{server}/api/jobs")
        assert e.value.code == 401

    def test_returns_the_batch_in_practice_mode(self, server):
        _ready(2)
        with _get(f"{server}/api/jobs", "secret123") as r:
            data = json.loads(r.read())
        assert data["mode"] == "practice"
        assert [a["title"] for a in data["apps"]] == ["Analyst 0", "Analyst 1"]
        assert "Military status (exempted / completed / postponed)" in data["missing"]


class TestApproveRoute:
    def _post(self, url, body, token="secret123"):
        req = urllib.request.Request(f"{url}/api/jobs_approve", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"})
        return urllib.request.urlopen(req, timeout=5)

    def test_sends_only_the_ticked_and_keeps_the_rest(self, server):
        a, b = _ready(2)
        with patch.object(pipeline, "start_in_background", return_value=True):
            with self._post(server, {"only": [a]}) as r:
                result = json.loads(r.read())["result"]
        assert result.startswith("Approved 1, skipped 0, 1 saved for later.")
        assert tracker.all_apps()[a]["status"] == "approved"
        assert tracker.all_apps()[b]["status"] == "ready"

    def test_nothing_ticked_sends_nothing(self, server):
        (a,) = _ready(1)
        with pytest.raises(urllib.error.HTTPError) as e:
            self._post(server, {"only": []})
        assert e.value.code == 400
        assert tracker.all_apps()[a]["status"] == "ready"

    def test_wrong_token_approves_nothing(self, server):
        (a,) = _ready(1)
        with pytest.raises(urllib.error.HTTPError) as e:
            self._post(server, {"only": [a]}, token="nope")
        assert e.value.code == 401
        assert tracker.all_apps()[a]["status"] == "ready"
