"""Remote rendering: the owner's PC (later Modal) runs the site's video jobs over /api/worker."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vidosuper.studio import accounts
from vidosuper.studio.remote import RemoteWorker
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.storage import user_dir, workdir
from vidosuper.studio.web.app import create_app
from vidosuper.studio.worker import Worker

TOKEN = "t" * 48


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    monkeypatch.setenv("VIDO_STUDIO_WORKER_TOKEN", TOKEN)
    s = load_settings(tmp_path / "server", {"brain": "local", "owner_brain": "local", "jobs": "remote"})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def _project(studio: Studio) -> tuple[dict, dict]:
    token = accounts.create_invite(studio.db, studio.s, "", "owner")
    user = accounts.register(studio.db, token, "owner", "a long password 1")
    p = studio.create_project(user, "Harbour", "9:16", 30, "a calm harbour film")
    work = workdir(studio.s, user["id"], p["id"])
    (work.clips / "c1.mp4").write_bytes(b"clip-bytes" * 1000)
    (user_dir(studio.s, user["id"]) / "profile.json").write_text('{"rules": []}', encoding="utf-8")
    return user, p


def _worker(server: Studio, tmp_path) -> RemoteWorker:
    return RemoteWorker(home=tmp_path / "pc", http=TestClient(create_app(server)), token=TOKEN)


def test_the_pc_runs_a_job_and_the_site_shows_it_done_only_once_the_video_has_arrived(server, tmp_path, monkeypatch):
    user, p = _project(server)
    server.db.enqueue("render", p["id"], user["id"])
    server.db.set_stage(p["id"], "rendering")
    pc = _worker(server, tmp_path)
    seen = {}

    def render_here(job, project, person):
        local = workdir(pc.studio.s, person["id"], project["id"])
        seen["clip"] = (local.clips / "c1.mp4").read_bytes()  # mirrored down before the job
        (local.out / "v1-9x16.mp4").write_bytes(b"video" * 500)
        pc.studio.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, "
                         "created) VALUES (?,?,?,?,?,?,?,?,?)", (project["id"], None, 1, "final", "9:16",
                                                                 "v1-9x16.mp4", 30.0, 12.0, 0.0))
        pc.studio.db.set_stage(project["id"], "done")
        seen["stage_during"] = server.db.project(project["id"])["stage"]  # held back until the upload

    monkeypatch.setattr(pc.studio, "_job_render", render_here)
    assert pc.tick() is True
    assert seen["clip"] == b"clip-bytes" * 1000 and seen["stage_during"] == "rendering"
    out = workdir(server.s, user["id"], p["id"]).out / "v1-9x16.mp4"
    assert out.read_bytes() == b"video" * 500
    assert server.db.project(p["id"])["stage"] == "done"
    assert server.db.one("SELECT status FROM jobs")["status"] == "done"
    assert pc.tick() is False  # nothing left; the second round downloads nothing new either
    assert server.renderer_seen() is not None and server.queued_note(None) == ""


def test_a_failed_upload_puts_the_job_back_instead_of_showing_a_missing_video(server, tmp_path, monkeypatch):
    user, p = _project(server)
    job_id = server.db.enqueue("render", p["id"], user["id"])
    pc = _worker(server, tmp_path)

    def render_here(job, project, person):
        (workdir(pc.studio.s, person["id"], project["id"]).out / "v1.mp4").write_bytes(b"x")
        pc.studio.db.set_stage(project["id"], "done")

    def broken(*a, **k):
        raise OSError("connection dropped")

    monkeypatch.setattr(pc.studio, "_job_render", render_here)
    monkeypatch.setattr(pc.mirror, "up", broken)
    with pytest.raises(OSError):
        pc.tick()
    job = server.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["status"] == "queued" and "send the result" in job["error"]
    assert server.db.project(p["id"])["stage"] != "done"


def test_the_worker_api_is_closed_without_the_right_token_and_stays_inside_a_persons_projects(server, tmp_path):
    user, p = _project(server)
    client = TestClient(create_app(server))
    assert client.post("/api/worker/claim").status_code == 404
    assert client.post("/api/worker/claim", headers={"Authorization": "Bearer wrong"}).status_code == 404
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/worker/claim", headers=auth).json()["job"] is None
    for bad in ("../secret.key", "/etc/passwd", "projects/../../x", "c:/x", "profile.json/../x", "uploads/x"):
        r = client.put(f"/api/worker/file/{user['id']}", params={"path": bad}, content=b"x", headers=auth)
        assert r.status_code == 400, bad
    server.s.jobs = "local"  # jobs run on the server itself: the API is shut
    assert TestClient(create_app(server)).post("/api/worker/claim", headers=auth).status_code == 404


def test_the_pc_forgets_projects_the_site_deleted_and_the_server_worker_leaves_jobs_alone(server, tmp_path):
    user, p = _project(server)
    pc = _worker(server, tmp_path)
    pc.mirror.down(user["id"], p["id"])
    local = workdir(pc.studio.s, user["id"], p["id"]).root
    assert (local / "clips" / "c1.mp4").exists()
    server.db.run("UPDATE projects SET deleted=1 WHERE id=?", (p["id"],))
    assert pc.purge() == 1 and not local.exists()

    server.db.enqueue("render", p["id"], user["id"])
    assert Worker(server).tick() is False  # remote mode: the server's own worker only keeps house
    assert server.db.one("SELECT status FROM jobs")["status"] == "queued"
    assert "renderer" in server.queued_note(server.db.one("SELECT * FROM jobs"))


def test_a_long_film_never_makes_a_short_step_wait_and_a_lane_starting_again_keeps_off_the_other_lanes_job(server, tmp_path):
    user, p = _project(server)
    other = server.create_project(user, "Second", "9:16", 30, "")
    film = server.db.enqueue("render", p["id"], user["id"], priority=6)
    step = server.db.enqueue("prepare", other["id"], user["id"], priority=5)
    client = TestClient(create_app(server))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    # the long lane takes the film even though the short step comes first in the queue; the quick lane takes the step
    assert client.post("/api/worker/claim", headers=auth, json={"lane": "long", "worker": "renderer"}).json()["job"]["id"] == film
    assert client.post("/api/worker/claim", headers=auth, json={"lane": "quick", "worker": "renderer-quick"}).json()["job"]["id"] == step
    assert client.post("/api/worker/claim", headers=auth, json={"lane": "long", "worker": "renderer"}).json()["job"] is None
    # the quick lane starting again puts back only its own job: the film keeps rendering
    assert client.post("/api/worker/recover", headers=auth, json={"worker": "renderer-quick"}).json()["requeued"] == 1
    assert server.db.one("SELECT status FROM jobs WHERE id=?", (film,))["status"] == "running"
    assert server.db.one("SELECT status FROM jobs WHERE id=?", (step,))["status"] == "queued"
    # an older renderer (no lane given) still takes anything
    assert client.post("/api/worker/claim", headers=auth).json()["job"]["id"] == step


def test_a_waiting_video_says_what_it_waits_for_and_its_time_counts_the_jobs_ahead(server, tmp_path):
    import time as _t

    user, p = _project(server)
    other = server.create_project(user, "Second", "9:16", 30, "")
    film = server.db.enqueue("render", p["id"], user["id"], priority=6)
    server.db.claim("long", "renderer")
    server.db.run("UPDATE jobs SET started=? WHERE id=?", (_t.time() - 60, film))
    step_id = server.db.enqueue("prepare", other["id"], user["id"], priority=5)
    step = server.db.one("SELECT * FROM jobs WHERE id=?", (step_id,))
    server.db.heartbeat("renderer", {})
    # one lane only: the step waits for the film, and says so
    assert "1 video is being made before yours" in server.queued_note(step)
    alone = server.expected_seconds(step)
    assert server.eta(step) > alone + 30
    # with the quick lane online the step waits for nothing
    server.db.heartbeat("renderer-quick", {})
    assert server.queued_note(step) == ""
    assert server.eta(step) == int(alone)
