"""The local test server with auto-reload (dev.cmd): uvicorn starts this factory again whenever a .py file changes,
so a fix is live without restarting by hand. The job worker runs inside it (jobs cut short by a reload are picked up
again when it starts)."""

from __future__ import annotations


def make():
    from ..cli import _logging
    from ..service import Studio
    from ..settings import settings as studio_settings
    from ..worker import Worker
    from .app import create_app

    _logging()
    studio = Studio(studio_settings())
    Worker(studio).start_thread()
    return create_app(studio)
