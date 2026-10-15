"""Put an improvement branch live, carefully: only if everything checks out, and undone by itself if the site does not
come back healthy.

Gates (any failure leaves the branch for the owner to look at, nothing live changes):
1. `improve_deploy: true` in studio.yaml (off by default).
2. The branch is a fast-forward of main and the working tree of main is clean (so nothing of the owner's is overwritten).
3. It touches none of the protected files (sign-in, uploads, settings, alerts, this routine itself, scripts).
4. The whole test suite passes in the branch's own worktree.
5. No job is running or queued (it waits up to `wait_minutes`; people's videos are never interrupted).
Then main is fast-forwarded, the studio restarted, and the site and its worker must answer within 90 s; otherwise main is
put back to the previous commit and the studio restarted again.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

PROTECTED = (
    "src/vidosuper/studio/accounts.py", "src/vidosuper/studio/storage.py", "src/vidosuper/studio/settings.py",
    "src/vidosuper/studio/alerts.py", "src/vidosuper/studio/deploy.py", "src/vidosuper/studio/improve.py",
    "scripts/", "CLAUDE.md", "docs/improve-prompt.md", "studio.cmd",
)
CREATE_NO_WINDOW = 0x08000000


class DeployRefused(RuntimeError):
    """Said to the owner as is: why nothing went live."""


def _git(repo: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300)
    if check and out.returncode != 0:
        raise DeployRefused(f"git {' '.join(args)}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def protected_hits(files: list[str]) -> list[str]:
    return [f for f in files if any(f == p or (p.endswith("/") and f.startswith(p)) for p in PROTECTED)]


def checks(repo: Path, branch: str) -> list[str]:
    """The files the branch changes, after the gates that need only git. Raises DeployRefused."""
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise DeployRefused("the live copy has uncommitted changes; left the branch for you")
    if _git(repo, "branch", "--show-current") != "main":
        raise DeployRefused("the live copy is not on main")
    ahead = _git(repo, "rev-list", "--count", f"main..{branch}")
    if ahead == "0":
        raise DeployRefused("the branch has no new commits")
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", "main", branch]).returncode != 0:
        raise DeployRefused("main moved on since the branch was made; it needs a rebase")
    files = [f for f in _git(repo, "diff", "--name-only", f"main...{branch}").splitlines() if f]
    if hit := protected_hits(files):
        raise DeployRefused("it changes protected files (" + ", ".join(hit[:3]) + "); you decide on those")
    return files


def run_tests(worktree: Path, python: str) -> None:
    env = {**os.environ, "PYTHONPATH": str(worktree / "src")}
    out = subprocess.run([python, "-m", "pytest", "-q", "-x"], cwd=str(worktree), env=env, capture_output=True, text=True,
                         timeout=3000)
    if out.returncode != 0:
        raise DeployRefused("tests failed in the branch: " + out.stdout.strip().splitlines()[-1][:160])


def wait_idle(studio, minutes: float) -> None:
    end = time.time() + minutes * 60
    while time.time() < end:
        busy = studio.db.value("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running') AND kind != 'learn'", (), 0)
        if not busy:
            return
        time.sleep(20)
    raise DeployRefused(f"people's videos were still being made after {minutes:.0f} minutes; try again later")


def restart_studio(repo: Path) -> None:
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*studio serve*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 3; "
        f"Start-Process cmd -ArgumentList '/c','start \"Flicello studio\" \"{repo / 'studio.cmd'}\"'"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=60,
                   creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)


def healthy(studio, port: int, seconds: float = 90.0) -> bool:
    end = time.time() + seconds
    time.sleep(12)
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/robots.txt", timeout=5) as r:  # noqa: S310 - our own address
                ok = r.status == 200
        except Exception:  # noqa: BLE001 - not up yet
            ok = False
        beat = studio.db.one("SELECT at FROM heartbeat WHERE name='worker'")
        if ok and beat and time.time() - beat["at"] < 30:
            return True
        time.sleep(5)
    return False


def deploy(studio, repo: Path, branch: str, worktree: Path, python: str, wait_minutes: float = 45.0) -> str:
    """Returns a one-line report for the owner. Raises DeployRefused when nothing went live."""
    if not studio.s.improve_deploy:
        raise DeployRefused("automatic deploy is off (improve_deploy in studio.yaml); the branch is waiting for you")
    files = checks(repo, branch)
    run_tests(worktree, python)
    wait_idle(studio, wait_minutes)
    checks(repo, branch)  # nothing moved while the tests ran
    before = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "-f", "last-good", before)
    _git(repo, "merge", "--ff-only", branch)
    restart_studio(repo)
    if healthy(studio, studio.s.port):
        return f"Deployed {branch}: {len(files)} files ({', '.join(files[:4])}). Rollback point: tag last-good."
    _git(repo, "reset", "--hard", before)
    restart_studio(repo)
    ok = healthy(studio, studio.s.port)
    raise DeployRefused(f"{branch} went live but the site did not answer: rolled back to {before[:7]}"
                        + ("" if ok else "; THE SITE IS STILL NOT ANSWERING, please check it"))
