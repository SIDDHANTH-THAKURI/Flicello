"""Cookieless visit counting and the owner's dashboard numbers."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from vidosuper.studio import accounts, analytics
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app

PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    st = Studio(s)
    analytics.ensure(st.db)
    return st


def test_a_visit_keeps_no_ip_no_ids_from_private_links_and_bots_are_not_counted(studio):
    db = studio.db
    assert analytics.record(db, path="/p/abc123def456", ip="5.6.7.8", ua=PHONE, ref="https://www.instagram.com/x",
                            host="flicello.com", utm=None, user_id=None)
    assert analytics.record(db, path="/join/SECRET-INVITE-TOKEN", ip="5.6.7.8", ua=PHONE, ref=None, host="flicello.com",
                            utm=None, user_id=None)
    assert not analytics.record(db, path="/", ip="1.1.1.1", ua="Googlebot/2.1", ref=None, host="x", utm=None, user_id=None)
    assert not analytics.record(db, path="/static/app.css", ip="1.1.1.1", ua=PHONE, ref=None, host="x", utm=None, user_id=None)
    rows = db.all("SELECT * FROM visits ORDER BY id")
    assert [r["path"] for r in rows] == ["/p/:id", "/join/:invite"]
    dump = str(rows)
    assert "5.6.7.8" not in dump and "SECRET" not in dump and "abc123" not in dump
    assert rows[0]["ref"] == "instagram.com" and rows[0]["device"] == "mobile" and rows[0]["browser"] == "Safari"
    assert rows[0]["visitor"] == rows[1]["visitor"]  # the same person, the same day: counted once a day


def test_yesterdays_salt_is_deleted_so_old_visitor_codes_cant_be_recomputed(studio):
    db = studio.db
    db.run("INSERT INTO salts(day, salt) VALUES (?,?)", ("2000-01-01", "old"))
    analytics.record(db, path="/", ip="5.6.7.8", ua=PHONE, ref=None, host="x", utm=None, user_id=None)
    assert [r["day"] for r in db.all("SELECT day FROM salts")] == [analytics.day_of(time.time())]


def test_own_site_and_utm_referrers(studio):
    assert analytics.referrer("https://flicello.com/legal", "flicello.com", None) is None
    assert analytics.referrer(None, "flicello.com", "Newsletter") == "newsletter"
    assert analytics.referrer("https://www.reddit.com/r/x", "flicello.com", None) == "reddit.com"


def test_pages_served_to_people_are_counted_through_the_app_and_the_owner_is_left_out(studio):
    client = TestClient(create_app(studio), headers={"user-agent": PHONE})
    client.get("/legal/privacy")
    client.get("/robots.txt")
    assert [r["path"] for r in studio.db.all("SELECT path FROM visits")] == ["/legal/privacy"]
    token = accounts.create_invite(studio.db, studio.s, "", "owner")
    accounts.register(studio.db, token, "boss", "a long password 1")
    client.post("/login", data={"name": "boss", "password": "a long password 1"})
    client.get("/legal/terms")
    m = analytics.metrics(studio.db, 7)
    assert m["kpi"]["visitors"][0] == 1  # the owner's own browsing is not a visitor
    assert m["funnel"][0]["stage"] == "Visitors" and len(m["days"]) == 7


def test_only_the_owner_reads_the_dashboard_numbers(studio):
    token = accounts.create_invite(studio.db, studio.s, "Ann")
    accounts.register(studio.db, token, "Ann", "a long password 2")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Ann", "password": "a long password 2"})
    assert client.get("/admin/api/metrics").status_code == 404
    assert client.get("/admin/api/live").status_code == 404
    token = accounts.create_invite(studio.db, studio.s, "", "owner")
    accounts.register(studio.db, token, "boss", "a long password 1")
    owner = TestClient(create_app(studio))
    owner.post("/login", data={"name": "boss", "password": "a long password 1"})
    r = owner.get("/admin/api/metrics?days=30")
    assert r.status_code == 200 and set(r.json()) >= {"kpi", "series", "funnel", "spend", "cohorts", "traffic"}
    assert owner.get("/admin").status_code == 200
