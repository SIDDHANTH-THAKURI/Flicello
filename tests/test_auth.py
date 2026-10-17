"""Public sign-up and sign-in: email codes, Google, consent, sessions that renew, erasure and export, legal pages."""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from vidosuper.studio import accounts, auth, mail
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app

SITE = "http://testserver"
SAME = {"origin": SITE}


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)  # credentials are blanked by conftest, not deleted here
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "public_signup": True,
                                         "mail_outbox": True})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def last_code(s) -> str:
    return re.search(r"code: (\d{6})", mail.outbox(s)[-1]["text"]).group(1)


# ---------------------------------------------------------------- email codes

def test_a_code_is_mailed_works_once_and_is_stored_only_as_a_hash(studio):
    email = auth.send_code(studio.db, studio.s, "  Mia@Example.COM ", "5.6.7.8")
    assert email == "mia@example.com"
    code = last_code(studio.s)
    assert code not in str(studio.db.all("SELECT * FROM login_codes"))  # never stored in plain text
    assert auth.check_code(studio.db, studio.s, "mia@example.com", code) == "mia@example.com"
    with pytest.raises(auth.AuthError, match="expired"):
        auth.check_code(studio.db, studio.s, "mia@example.com", code)


def test_wrong_codes_run_out_and_old_codes_expire(studio):
    auth.send_code(studio.db, studio.s, "sam@example.com", "5.6.7.8")
    code = last_code(studio.s)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(auth.CODE_TRIES):
        with pytest.raises(auth.AuthError):
            auth.check_code(studio.db, studio.s, "sam@example.com", wrong)
    with pytest.raises(auth.AuthError, match="Too many"):
        auth.check_code(studio.db, studio.s, "sam@example.com", code)  # even the right one, now
    auth.send_code(studio.db, studio.s, "sam@example.com", "5.6.7.8")
    studio.db.run("UPDATE login_codes SET expires=?", (time.time() - 1,))
    with pytest.raises(auth.AuthError, match="expired"):
        auth.check_code(studio.db, studio.s, "sam@example.com", last_code(studio.s))


def test_the_form_cant_be_used_to_flood_an_inbox_or_to_sign_up_with_a_throwaway_address(studio):
    for _ in range(5):
        auth.send_code(studio.db, studio.s, "flood@example.com", "5.6.7.8")
    with pytest.raises(auth.AuthError, match="a few codes"):
        auth.send_code(studio.db, studio.s, "flood@example.com", "9.9.9.9")
    with pytest.raises(auth.AuthError, match="throwaway"):
        auth.normal_email("x@mailinator.com")
    with pytest.raises(auth.AuthError):
        auth.normal_email("not an email")


def test_without_an_email_service_the_page_says_so_instead_of_failing(studio):
    studio.s.mail_outbox = False
    with pytest.raises(auth.AuthError, match="Google"):
        auth.send_code(studio.db, studio.s, "mia@example.com", "5.6.7.8")


# ---------------------------------------------------------------- the whole sign-up through the web

def signup(client, studio, email="ava@example.com", name="Ava"):
    r = client.post("/auth/email", data={"email": email, "mode": "signup"}, headers=SAME)
    assert r.status_code == 200 and "Check your" in r.text
    r = client.post("/auth/email/verify", data={"email": email, "code": last_code(studio.s), "mode": "signup"},
                    headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/join"
    return client.post("/join", data={"display": name, "adult": "yes", "agree": "yes"}, headers=SAME,
                       follow_redirects=False)


def test_a_new_person_signs_up_with_an_email_code_confirms_18_and_the_terms_and_is_signed_in(studio):
    client = TestClient(create_app(studio))
    r = client.post("/auth/email", data={"email": "ava@example.com", "mode": "signup"}, headers=SAME)
    r = client.post("/auth/email/verify", data={"email": "ava@example.com", "code": last_code(studio.s)},
                    headers=SAME, follow_redirects=False)
    assert r.headers["location"] == "/join" and "vs_pending" in r.headers["set-cookie"]
    assert "Nearly" in client.get("/join").text
    r = client.post("/join", data={"display": "Ava", "agree": "yes"}, headers=SAME)  # no 18+ tick
    assert "18" in r.text and not studio.db.one("SELECT id FROM users")
    r = client.post("/join", data={"display": "Ava", "adult": "yes", "agree": "yes"}, headers=SAME,
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/welcome" and "vs_session" in r.headers["set-cookie"]
    user = studio.db.one("SELECT * FROM users")
    assert user["email"] == "ava@example.com" and user["display"] == "Ava" and user["pw"] == "" and user["adult"]
    consents = {c["kind"]: c for c in studio.db.all("SELECT * FROM consents WHERE user_id=?", (user["id"],))}
    assert set(consents) == {"age_18", "terms", "privacy", "marketing"}
    assert consents["terms"]["version"] == auth.LEGAL_VERSION and consents["marketing"]["granted"] == 0
    assert consents["terms"]["ip"] and "5.6.7.8" not in str(consents)  # a keyed hash, not the address
    assert client.get("/account").status_code == 200


def test_coming_back_with_a_code_signs_the_same_person_in(studio):
    client = TestClient(create_app(studio))
    signup(client, studio)
    client.cookies.clear()
    client.post("/auth/email", data={"email": "AVA@example.com"}, headers=SAME)
    r = client.post("/auth/email/verify", data={"email": "ava@example.com", "code": last_code(studio.s), "next": "/style"},
                    headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/style" and "vs_session" in r.headers["set-cookie"]
    assert studio.db.value("SELECT COUNT(*) FROM users", (), 0) == 1


def test_when_sign_up_is_closed_a_stranger_with_a_code_still_gets_no_account(studio):
    studio.s.public_signup = False
    client = TestClient(create_app(studio))
    client.post("/auth/email", data={"email": "eve@example.com"}, headers=SAME)
    r = client.post("/auth/email/verify", data={"email": "eve@example.com", "code": last_code(studio.s)}, headers=SAME)
    assert "invitation" in r.text and not studio.db.one("SELECT id FROM users")


def test_sign_in_forms_posted_from_another_site_are_refused(studio):
    client = TestClient(create_app(studio))
    r = client.post("/auth/email", data={"email": "ava@example.com"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and not mail.outbox(studio.s)
    r = client.post("/auth/email", data={"email": "ava@example.com"})  # neither Origin nor Referer
    assert r.status_code == 403
    r = client.post("/auth/email", data={"email": "ava@example.com"}, headers={"origin": "null"})
    assert r.status_code == 403


def test_pages_keep_the_referrer_on_our_own_site_so_browsers_send_our_origin_with_forms(studio):
    # with "no-referrer", browsers send `Origin: null` even for same-site form posts, and sign-in would break
    client = TestClient(create_app(studio))
    assert client.get("/signin").headers["referrer-policy"] == "same-origin"


def test_after_sign_in_people_only_go_to_pages_on_this_site():
    from vidosuper.studio.web.authweb import safe_next

    assert safe_next("/p/abc") == "/p/abc"
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", None, ""):
        assert safe_next(bad) == "/"


# ---------------------------------------------------------------- Google

def test_google_sign_in_checks_state_and_nonce_then_makes_or_finds_the_account(studio, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    url, state = auth.google_start(studio.db, f"{SITE}/auth/google/callback", "/new")
    assert "code_challenge_method=S256" in url and "nonce=" in url and state in url
    nonce = re.search(r"nonce=([^&]+)", url).group(1)
    exchange = lambda code, verifier: {"id_token": "t"}  # noqa: E731
    good = lambda t: {"sub": "g-1", "email": "Zoe@Example.com", "email_verified": True, "nonce": nonce, "name": "Zoe"}  # noqa: E731
    with pytest.raises(auth.AuthError, match="browser"):
        auth.google_finish(studio.db, "x", state, "c", "another-state", exchange, good)
    url, state = auth.google_start(studio.db, f"{SITE}/auth/google/callback", "/new")
    nonce = re.search(r"nonce=([^&]+)", url).group(1)
    with pytest.raises(auth.AuthError, match="checked"):
        auth.google_finish(studio.db, "x", state, "c", state, exchange, lambda t: {**good(t), "nonce": "replayed"})
    url, state = auth.google_start(studio.db, f"{SITE}/auth/google/callback", "/new")
    nonce = re.search(r"nonce=([^&]+)", url).group(1)
    claims, nxt = auth.google_finish(studio.db, "x", state, "c", state, exchange,
                                     lambda t: {"sub": "g-1", "email": "zoe@example.com", "email_verified": True,
                                                "nonce": nonce})
    assert claims["sub"] == "g-1" and nxt == "/new"
    with pytest.raises(auth.AuthError, match="too long"):  # a state is good once
        auth.google_finish(studio.db, "x", state, "c", state, exchange, good)


def test_an_unverified_google_email_is_refused(studio, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    url, state = auth.google_start(studio.db, "x", "/")
    nonce = re.search(r"nonce=([^&]+)", url).group(1)
    with pytest.raises(auth.AuthError, match="verified"):
        auth.google_finish(studio.db, "x", state, "c", state, lambda c, v: {"id_token": "t"},
                           lambda t: {"sub": "g", "email": "a@b.co", "email_verified": False, "nonce": nonce})


# ---------------------------------------------------------------- sessions

def test_an_active_sign_in_keeps_renewing_itself_but_never_past_a_year(studio):
    client = TestClient(create_app(studio))
    signup(client, studio)
    sess = studio.db.one("SELECT * FROM sessions")
    studio.db.run("UPDATE sessions SET expires=?", (time.time() + 5 * 86400,))  # nearly over
    r = client.get("/account")
    assert r.status_code == 200 and "vs_session" in r.headers.get("set-cookie", "")
    assert studio.db.one("SELECT expires FROM sessions")["expires"] > time.time() + 50 * 86400
    created = time.time() - 340 * 86400  # signed in 340 days ago, still active: renewed only up to day 365
    studio.db.run("UPDATE sessions SET created=?, expires=?", (created, time.time() + 86400))
    client.get("/account")
    row = studio.db.one("SELECT created, expires FROM sessions")
    assert abs(row["expires"] - (created + 365 * 86400)) < 5
    assert sess["token"] == studio.db.one("SELECT token FROM sessions")["token"]  # same sign-in, not a new one


# ---------------------------------------------------------------- the person's rights

def test_export_has_everything_we_keep_and_deleting_the_account_erases_it(studio):
    client = TestClient(create_app(studio))
    signup(client, studio)
    user = studio.db.one("SELECT * FROM users")
    data = client.get("/account/export").json()
    assert data["account"]["email"] == "ava@example.com" and len(data["consents"]) == 4
    csrf = studio.db.one("SELECT csrf FROM sessions")["csrf"]
    r = client.post("/account/delete", data={"csrf": csrf, "sure": "nope"}, follow_redirects=False)
    assert studio.db.one("SELECT id FROM users")
    r = client.post("/account/delete", data={"csrf": csrf, "sure": "delete"}, follow_redirects=False)
    assert r.status_code == 303
    for table in ("users", "sessions", "consents", "devices"):
        assert not studio.db.all(f"SELECT * FROM {table}"), table
    assert not studio.db.all("SELECT * FROM audit WHERE user_id=?", (user["id"],))
    assert studio.db.one("SELECT action FROM audit WHERE action='account_deleted'")


def test_the_owner_cant_delete_their_own_account(studio):
    from vidosuper.studio.service import ActionError

    token = accounts.create_invite(studio.db, studio.s, "", "owner")
    owner = accounts.register(studio.db, token, "owner", "a long password 1")
    with pytest.raises(ActionError):
        studio.delete_account(owner)


# ---------------------------------------------------------------- legal pages

def test_every_legal_page_renders_with_its_facts_filled_in(studio):
    from vidosuper.studio.web.legalweb import ORDER

    client = TestClient(create_app(studio))
    for slug in ORDER:
        r = client.get(f"/legal/{slug}")
        assert r.status_code == 200, slug
        assert "{{" not in r.text and "Siddhanth Thakuri" in r.text, slug
    assert client.get("/legal").status_code == 200
    assert client.get("/legal/../app").status_code == 404
    assert "Contact: mailto:security@flicello.com" in client.get("/.well-known/security.txt").text


def test_the_public_face_is_always_indexable_and_private_pages_never_are(studio):
    """Google's brand check (and search) must be able to read the home page and the legal pages even while sign-up is
    closed; a signed-in page, or /signup while nobody can join, never is."""
    client = TestClient(create_app(studio))
    for path in ("/", "/signin", "/signup", "/legal/privacy"):
        assert client.get(path).headers["x-robots-tag"].startswith("index"), path
    assert 'name="robots"' not in client.get("/legal/privacy").text
    assert "Disallow: /p/" in client.get("/robots.txt").text
    signup(client, studio)
    assert "noindex" in client.get("/").headers["x-robots-tag"]  # the dashboard, not the landing
    assert 'name="robots"' in client.get("/account").text

    studio.s.public_signup = False
    client = TestClient(create_app(studio))
    for path in ("/", "/signin", "/legal/privacy"):
        assert client.get(path).headers["x-robots-tag"].startswith("index"), path
    assert "noindex" in client.get("/signup").headers["x-robots-tag"]
    robots = client.get("/robots.txt").text
    assert "Disallow: /signup\n" in robots and "Disallow: /admin\n" in robots and "Disallow: /\n" not in robots


def test_an_invite_lets_someone_join_with_an_email_code_while_public_sign_up_is_closed(studio):
    studio.s.public_signup = False
    token = accounts.create_invite(studio.db, studio.s, "Mo")
    client = TestClient(create_app(studio))
    r = client.get(f"/join/{token}")
    assert r.status_code == 200 and "Mo" in r.text and "vs_invite" in r.headers["set-cookie"]
    client.post("/auth/email", data={"email": "mo@example.com", "mode": "signup"}, headers=SAME)
    r = client.post("/auth/email/verify", data={"email": "mo@example.com", "code": last_code(studio.s)}, headers=SAME,
                    follow_redirects=False)
    assert r.headers["location"] == "/join"
    r = client.post("/join", data={"display": "Mo", "adult": "yes", "agree": "yes"}, headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/welcome"
    user = studio.db.one("SELECT * FROM users WHERE email='mo@example.com'")
    assert user["role"] == "friend" and user["joined_by"] == "invite"
    assert studio.db.one("SELECT used_by FROM invites")["used_by"] == user["id"]  # spent
    assert client.get(f"/join/{token}").status_code == 404


def test_an_owner_invite_makes_the_owner_and_a_stranger_without_one_still_cant_join(studio):
    studio.s.public_signup = False
    token = accounts.create_invite(studio.db, studio.s, "", "owner")
    client = TestClient(create_app(studio))
    client.get(f"/join/{token}")
    client.post("/auth/email", data={"email": "boss@example.com"}, headers=SAME)
    client.post("/auth/email/verify", data={"email": "boss@example.com", "code": last_code(studio.s)}, headers=SAME)
    client.post("/join", data={"display": "Boss", "adult": "yes", "agree": "yes"}, headers=SAME)
    assert studio.db.one("SELECT role FROM users WHERE email='boss@example.com'")["role"] == "owner"
    stranger = TestClient(create_app(studio))
    stranger.post("/auth/email", data={"email": "eve@example.com"}, headers=SAME)
    r = stranger.post("/auth/email/verify", data={"email": "eve@example.com", "code": last_code(studio.s)}, headers=SAME)
    assert "invitation" in r.text and not studio.db.one("SELECT id FROM users WHERE email='eve@example.com'")


def test_someone_with_a_password_account_adds_an_email_proven_by_a_code(studio):
    token = accounts.create_invite(studio.db, studio.s, "Lou")
    accounts.register(studio.db, token, "Lou", "a long password 9")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Lou", "password": "a long password 9"})
    csrf = studio.db.one("SELECT csrf FROM sessions")["csrf"]
    page = client.post("/account/email", data={"csrf": csrf, "email": "lou@example.com"})
    assert "Check your" in page.text
    r = client.post("/account/email/verify", data={"csrf": csrf, "email": "lou@example.com", "code": "000000"
                                                    if last_code(studio.s) != "000000" else "111111"})
    assert "right" in r.text and not studio.db.one("SELECT email FROM users")["email"]
    r = client.post("/account/email/verify", data={"csrf": csrf, "email": "lou@example.com", "code": last_code(studio.s)},
                    follow_redirects=False)
    assert r.status_code == 303 and studio.db.one("SELECT email FROM users")["email"] == "lou@example.com"
    client.cookies.clear()  # and now a code signs Lou in
    client.post("/auth/email", data={"email": "lou@example.com"}, headers=SAME)
    r = client.post("/auth/email/verify", data={"email": "lou@example.com", "code": last_code(studio.s)}, headers=SAME,
                    follow_redirects=False)
    assert r.status_code == 303 and "vs_session" in r.headers["set-cookie"]


def test_with_an_allow_list_only_those_emails_may_join_or_sign_in(studio):
    studio.s.public_signup = False
    studio.s.allowed_emails = ["ava@example.com"]
    client = TestClient(create_app(studio))
    client.post("/auth/email", data={"email": "eve@example.com"}, headers=SAME)  # not on the list: no account
    r = client.post("/auth/email/verify", data={"email": "eve@example.com", "code": last_code(studio.s)}, headers=SAME)
    assert "invitation" in r.text and not studio.db.one("SELECT id FROM users")
    signup(client, studio)  # on the list: joins although public sign-up is closed
    ava = studio.db.one("SELECT * FROM users WHERE email='ava@example.com'")
    assert ava is not None
    studio.s.allowed_emails = ["someone@else.com"]  # taken off the list: can't sign in any more
    with pytest.raises(auth.AuthError):
        auth.sign_in(studio.db, studio.s, ava, "email", "1.2.3.4")
    owner = {**ava, "role": "owner"}  # the owner and invited people always may
    assert auth.may_use(studio.s, owner) and auth.may_use(studio.s, {**ava, "joined_by": "invite"})


def _owner_client(studio, email="boss@example.com"):
    studio.db.run("INSERT INTO users(id, name, role, pw, created, email, display, onboarded) VALUES (?,?,?,?,?,?,?,1)",
                  ("u" + "0" * 12, "boss", "owner", "", time.time(), email, "Boss"))
    client = TestClient(create_app(studio))
    client.cookies.set("vs_session", accounts.new_session(studio.db, studio.s, "u" + "0" * 12, "1.2.3.4"))
    return client


def _csrf(client, path="/admin"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def test_in_the_beta_a_stranger_asks_for_access_and_can_only_make_videos_once_the_owner_approves(studio):
    studio.s.public_signup = False
    studio.s.beta = True
    studio.s.allowed_emails = ["ava@example.com"]
    boss = _owner_client(studio)
    # someone on the list is in at once
    signup(TestClient(create_app(studio)), studio)
    assert studio.db.one("SELECT access FROM users WHERE email='ava@example.com'")["access"] == "beta"
    # a stranger may make an account, but it waits
    eve = TestClient(create_app(studio))
    r = signup(eve, studio, "eve@example.com", "Eve")
    assert r.status_code == 303
    row = studio.db.one("SELECT * FROM users WHERE email='eve@example.com'")
    assert row["access"] == "requested" and row["requested"]
    for path in ("/", "/new", "/welcome", "/style"):
        r = eve.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/access", path
    assert eve.get("/api/p/pnope/status").status_code == 403
    assert eve.get("/account").status_code == 200  # their account (export, delete) works while waiting
    page = eve.get("/access").text
    assert "invite-only" in page
    # asking tells the owner once, by email
    before = len(mail.outbox(studio.s))
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    eve.post("/access", data={"csrf": csrf, "note": "Reels from my trips"})
    sent = mail.outbox(studio.s)[before:]
    assert [m["to"] for m in sent] == ["boss@example.com"] and "Reels from my trips" in sent[0]["text"]
    assert "on the list" in eve.get("/access").text
    # the owner sees the request and approves it as special: Eve is told, and may make long films
    admin = boss.get("/admin").text
    assert "Asking for access" in admin and "Reels from my trips" in admin
    boss.post("/admin/user", data={"csrf": _csrf(boss), "uid": row["id"], "action": "access", "access": "special"})
    assert studio.db.one("SELECT access FROM users WHERE id=?", (row["id"],))["access"] == "special"
    assert mail.outbox(studio.s)[-1]["to"] == "eve@example.com" and "10 minutes" in mail.outbox(studio.s)[-1]["text"]
    assert eve.get("/access", follow_redirects=False).headers["location"] == "/"
    new = eve.get("/new").text
    assert 'value="600"' in new and "10 min" in new
    # a beta person can't ask for a long film, and sees that special access has them
    ava = studio.db.one("SELECT * FROM users WHERE email='ava@example.com'")
    assert studio.lengths_for(ava)[-1] == 60
    with pytest.raises(Exception, match="length"):
        studio.create_project(ava, "x", "9:16", 600, "")
    assert studio.create_project({**row, "access": "special", "videos_allowed": None}, "x", "9:16", 600, "")


def test_a_declined_person_is_told_kindly_and_still_cant_make_videos(studio):
    studio.s.beta = True
    boss = _owner_client(studio)
    eve = TestClient(create_app(studio))
    signup(eve, studio, "eve@example.com", "Eve")
    uid = studio.db.one("SELECT id FROM users WHERE email='eve@example.com'")["id"]
    boss.post("/admin/user", data={"csrf": _csrf(boss), "uid": uid, "action": "access", "access": "declined"})
    assert "Not this time" in eve.get("/access").text
    assert eve.get("/new", follow_redirects=False).headers["location"] == "/access"
