"""The cloud renderer: Modal containers running the same remote worker as the owner's PC (remote.py, cloud=True),
started by the site when there is work the PC isn't taking, and stopping by themselves when the queue is empty.

    deploy/modal_app.py     the containers (`work`), the models volume, and the `wake` address the site calls
    VIDO_STUDIO_MODAL_URL   that address, on the server; VIDO_STUDIO_MODAL = auto (default) | always | off

auto: the PC is free and goes first; the cloud takes everything while the PC is offline, and a step that has waited
`modal_wait` seconds while the PC is busy. always: every step the cloud can run goes there at once. The owner's own
videos always stay on the PC: they use the owner's Claude plan (Claude Code), which exists only there.
"""

from __future__ import annotations

import logging
import os
import time

from .alerts import alert

log = logging.getLogger("vidosuper.studio.cloud")

CORES, MEMORY_GIB = 8, 16  # one cloud renderer (deploy/modal_app.py): 8 physical cores (16 vCPUs), 16 GiB
PRICE_CORE_SECOND = 0.0000131  # USD, Modal's list prices (October 2026)
PRICE_GIB_SECOND = 0.00000222
PC_NAMES = ("renderer", "renderer-quick")
PC_ONLINE = 90.0  # seconds since a PC lane last checked in
STARTING = 120.0  # a renderer woken this long ago may still be starting: don't wake another for the same work


def usd_per_second() -> float:
    return CORES * PRICE_CORE_SECOND + MEMORY_GIB * PRICE_GIB_SECOND


def configured(s) -> bool:
    return s.jobs == "remote" and bool(s.modal_url) and s.modal_mode != "off"


def pc_only_users(studio) -> list[str]:
    """People whose steps can't run in the cloud: the owner, while their videos use Claude Code."""
    if studio.s.owner_brain != "claude_code":
        return []
    return [r["id"] for r in studio.db.all("SELECT id FROM users WHERE role='owner'")]


def cloud_jobs(studio, now: float | None = None) -> list[dict]:
    """Steps waiting now that a cloud renderer may take (learning a style from a rating can wait for the PC)."""
    now = time.time() if now is None else now
    skip = set(pc_only_users(studio))
    rows = studio.db.all("SELECT * FROM jobs WHERE status='queued' AND (after IS NULL OR after <= ?) AND kind != 'learn'",
                         (now,))
    return [r for r in rows if r["user_id"] not in skip or r["user_id"] is None]


def alive(db, now: float | None = None, within: float = 60.0) -> int:
    """Cloud renderers that checked in lately."""
    now = time.time() if now is None else now
    return int(db.value("SELECT COUNT(*) FROM heartbeat WHERE name LIKE 'modal-%' AND at > ?", (now - within,), 0) or 0)


def pc_online(db, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    at = db.value("SELECT MAX(at) FROM heartbeat WHERE name IN (?, ?)", PC_NAMES, None)
    return at is not None and now - at < PC_ONLINE


def can_take(studio, job: dict) -> bool:
    """A cloud renderer could take this step (so a waiting person can be told it is starting there)."""
    return configured(studio.s) and job.get("user_id") not in set(pc_only_users(studio)) and job["kind"] != "learn"


def wake(studio, now: float | None = None, post=None) -> str:
    """Start cloud renderers for the work that needs them (see the module's note); returns what it did ("" if
    nothing). Called by the site's worker every few seconds; never raises."""
    import json

    s, db = studio.s, studio.db
    if not configured(s):
        return ""
    now = time.time() if now is None else now
    try:
        jobs = cloud_jobs(studio, now)
        if not jobs:
            return ""
        cloud_only = any(j["kind"] == "selftest" for j in jobs)  # no PC ever takes these: no point waiting for one
        if s.modal_mode == "auto" and pc_online(db, now) and not cloud_only:
            waited = now - min(max(j["created"], j["after"] or 0.0) for j in jobs)
            if waited < s.modal_wait:
                return ""  # the PC is online and takes it in a moment
        last = db.one("SELECT at, info FROM heartbeat WHERE name='cloud-wake'")
        starting = 0
        if last and now - last["at"] < STARTING:
            starting = int((json.loads(last["info"] or "{}") or {}).get("n", 0))
        running = alive(db, now)
        want = min(len(jobs), max(1, int(s.modal_max))) - max(running, starting)
        if want <= 0:
            return ""
        token = os.environ.get("VIDO_STUDIO_WORKER_TOKEN", "")
        if post is None:
            import httpx

            post = httpx.post
        r = post(s.modal_url, json={"n": want, "why": f"{len(jobs)} step(s) waiting"},
                 headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
        if getattr(r, "status_code", 200) >= 400:
            raise RuntimeError(f"the cloud renderer answered {r.status_code}")
        db.heartbeat("cloud-wake", {"n": want + starting, "jobs": len(jobs)})  # all that may still be starting
        return f"started {want} cloud renderer{'s' if want != 1 else ''} for {len(jobs)} waiting step(s)"
    except Exception as exc:  # noqa: BLE001 - the site goes on; the owner hears about it (once an hour at most)
        log.warning("could not wake the cloud renderer: %s", exc)
        seen = db.value("SELECT MAX(at) FROM alerts WHERE kind='cloud'", (), None)
        if not seen or now - seen > 3600:
            alert(db, s, "cloud", f"The cloud renderer could not be started: {exc}"[:300], "warning")
        return ""


def month_usd(db) -> float:
    import calendar

    start = calendar.timegm(time.strptime(time.strftime("%Y-%m-01", time.gmtime()), "%Y-%m-%d"))
    return float(db.value("SELECT COALESCE(SUM(usd), 0) FROM spend WHERE backend='modal' AND at >= ?", (start,), 0) or 0)


def status(studio) -> dict:
    """For the admin page."""
    s, db = studio.s, studio.db
    last = db.one("SELECT at FROM heartbeat WHERE name='cloud-wake'")
    test = db.one("SELECT * FROM jobs WHERE kind='selftest' ORDER BY id DESC LIMIT 1")
    return {"on": configured(s), "mode": s.modal_mode if s.modal_url else "not set up", "max": s.modal_max,
            "running": alive(db), "woke": last["at"] if last else None, "month_usd": round(month_usd(db), 2),
            "test": test}


def request_test(studio) -> int:
    """The owner asks a cloud renderer to prove itself: a self-test step no PC takes (see selftest.py)."""
    from .service import ActionError

    if not configured(studio.s):
        raise ActionError("The cloud renderer isn't set up: set VIDO_STUDIO_MODAL_URL on the server.")
    if studio.db.one("SELECT id FROM jobs WHERE kind='selftest' AND status IN ('queued','running')"):
        raise ActionError("A test is already on its way.")
    return studio.db.enqueue("selftest", None, None, {}, 1)
