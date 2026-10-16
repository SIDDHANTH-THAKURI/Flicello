from __future__ import annotations

import sys

import pytest

from vidosuper.screen import SCREENSHOT_SCRIPT, capture_screen, screen_size


def test_script_sets_dpi_awareness_before_reading_screen_metrics():
    text = SCREENSHOT_SCRIPT.read_text(encoding="utf-8")
    assert (text.index("[VidoDpi]::SetProcessDPIAware()") < text.index("SystemInformation]::VirtualScreen")
            < text.index("$graphics.CopyFromScreen"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows screen capture")
def test_capture_writes_png_and_prints_its_path(tmp_path):
    png = capture_screen(tmp_path / "shot.png")
    assert png == (tmp_path / "shot.png").resolve()
    w, h = screen_size(png)
    assert w > 0 and h > 0
