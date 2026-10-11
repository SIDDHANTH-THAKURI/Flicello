"""Alerts for the owner: a failed job, a stopped worker, low disk, the AI budget nearly used up.

Every alert goes into the database (the admin page lists them) and the log. If `alert_url` is set (for
example an ntfy.sh topic the owner follows on their phone), a short plain-text line is POSTed there too:
never footage, file names or anything a friend wrote. The same kind of alert is sent at most once per
half hour so a failing loop can't flood the phone.
"""

from __future__ import annotations

import logging
import time
import urllib.request

from .db import DB
from .settings import StudioSettings

log = logging.getLogger("vidosuper.studio")
QUIET = 1800.0


def alert(db: DB, s: StudioSettings, kind: str, message: str, level: str = "error") -> None:
    recent = db.value("SELECT MAX(at) FROM alerts WHERE kind=?", (kind,))
    db.run("INSERT INTO alerts(at, level, kind, message) VALUES (?,?,?,?)", (time.time(), level, kind, message[:500]))
    (log.error if level == "error" else log.warning)("ALERT %s: %s", kind, message)
    if not s.alert_url or (recent and time.time() - recent < QUIET):
        return
    try:
        req = urllib.request.Request(s.alert_url, data=f"[{s.name}] {message[:300]}".encode("utf-8"),
                                     headers={"Title": f"{s.name}: {kind}", "Priority": "high" if level == "error" else "default"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=5).close()  # noqa: S310 - the owner's own alert address
    except Exception as exc:  # noqa: BLE001 - an alert that can't be sent must never break the job that raised it
        log.warning("could not send the alert: %s", exc)
