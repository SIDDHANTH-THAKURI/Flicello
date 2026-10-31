"""The person in a designed frame, measured: whatever the scene, Flicello knows where the person is (their cut-out),
so it can test, not guess, three things a reviewer looking at stills misses:

- something covering their face (a line, a shape, a glow drawn over it),
- them cut off at the frame's edge where their own shot wasn't,
- them shrunk far below their own size.

Each time is measured from three pictures: the frame as the video shows it (`full`), the person alone as the scene
places them (`alone`, the same camera and placement with everything else hidden) and their mask there (`mask`), plus
their cut-out in their own shot (`source`). The pictures come from the engine (a HyperFrames page or a Blender
scene); this module only measures.
"""

from __future__ import annotations

from dataclasses import dataclass

COVER_DIFF = 80  # a pixel of the face this far from the person alone (0-255, any channel) is covered
COVER_SHARE = 0.08  # this share of the face covered is a problem
ACROSS = 0.6  # ...or a line crossing it: rows of their head covered for this share of its width
EDGE_SHARE = 0.12  # their outline along a frame edge for this share of it: they're cut off there
SMALL_AREA = 0.45  # their area this share of their own shot's (about 2/3 their size) or less: shrunk


@dataclass
class Seen:
    t: float
    covered: float = 0.0  # share of the face covered
    crossed: bool = False  # something (a line) drawn right across it
    cut: tuple[str, ...] = ()  # edges they're cut off at that their shot wasn't
    size: float = 1.0  # their area against their own shot's
    face: bool = False  # a face (or head) was found


def _edges(on, share: float) -> set[str]:
    h, w = on.shape
    out = set()
    if on[:, :2].any(axis=1).sum() > share * h:
        out.add("left")
    if on[:, -2:].any(axis=1).sum() > share * h:
        out.add("right")
    if on[:2, :].any(axis=0).sum() > share * w:
        out.add("top")
    return out


def face_box(alone, on) -> tuple[int, int, int, int] | None:
    """The face in the person-alone picture: a detected frontal face on them, else their head (the top of their
    outline). (x0, y0, x1, y1) in pixels."""
    import cv2
    import numpy as np

    ys, xs = np.where(on)
    if not len(ys):
        return None
    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        grey = cv2.cvtColor(alone, cv2.COLOR_BGR2GRAY)
        small = max(24, int(0.04 * alone.shape[0]))
        faces = [] if cascade.empty() else cascade.detectMultiScale(grey, 1.1, 5, minSize=(small, small))
    except (AttributeError, cv2.error):
        faces = []
    on_them = [(x, y, fw, fh) for x, y, fw, fh in faces if on[min(on.shape[0] - 1, y + fh // 2),
                                                              min(on.shape[1] - 1, x + fw // 2)]]
    if on_them:  # their face, and their chin and neck below it
        x, y, fw, fh = max(on_them, key=lambda b: b[2] * b[3])
        return int(x), int(y), int(x + fw), min(on.shape[0], int(y + 1.3 * fh))
    return head_box(on)


def head_box(on) -> tuple[int, int, int, int] | None:
    """Their head (hair to neck) from their outline alone: from its top down to where the shoulders widen it."""
    import numpy as np

    widths = on.sum(axis=1)
    rows = np.where(widths > 0)[0]
    if not len(rows):
        return None
    top, bottom = int(rows[0]), int(rows[-1])
    span = bottom - top
    head_w = int(widths[top:top + max(2, int(0.15 * span))].max())
    end = bottom + 1
    for r in range(top + int(0.6 * head_w), bottom + 1):
        if widths[r] > 1.6 * head_w:
            end = r
            break
    end = min(end, top + int(1.6 * head_w))  # never the whole body
    cols = np.where(on[top:end].any(axis=0))[0]
    if not len(cols):
        return None
    return int(cols.min()), top, int(cols.max()) + 1, end


def measure(t: float, full, alone, mask, source) -> Seen:
    """One time measured (pictures as BGR uint8 arrays; `mask` and `source` 0-255 single channel, any size)."""
    import cv2
    import numpy as np

    h, w = full.shape[:2]
    if alone.shape[:2] != (h, w):
        alone = cv2.resize(alone, (w, h))
    on = cv2.resize(mask, (w, h)) > 127
    src = cv2.resize(source, (w, h)) > 127 if source is not None else None
    seen = Seen(t)
    if src is not None and src.sum() > 0:
        seen.size = round(float(on.sum()) / float(src.sum()), 3)
        seen.cut = tuple(sorted(_edges(on, EDGE_SHARE) - _edges(src, EDGE_SHARE)))
    box = face_box(alone, on)
    if box is not None:
        x0, y0, x1, y1 = box
        zone = on[y0:y1, x0:x1]
        if zone.sum() > 0:
            seen.face = True
            diff = np.abs(full[y0:y1, x0:x1].astype(np.int16) - alone[y0:y1, x0:x1].astype(np.int16)).max(axis=2)
            cov = zone & (diff > COVER_DIFF)
            seen.covered = round(float(cov.sum()) / float(zone.sum()), 3)
            # a thin line across their head covers little of it but crosses it: rows covered most of their width
            across = (cov.sum(axis=1) > ACROSS * np.maximum(zone.sum(axis=1), 1)) & (zone.sum(axis=1) > 0)
            seen.crossed = int(across.sum()) >= 2
    return seen


def problems(seen: list[Seen]) -> list[str]:
    """What the measures say, one line per kind of problem with its times, worded as fixes."""
    out = []
    covered = [s for s in seen if s.face and (s.covered > COVER_SHARE or s.crossed)]
    if covered:
        worst = max(covered, key=lambda s: s.covered)
        how = "something crosses their face" if all(s.covered <= COVER_SHARE for s in covered) else \
            f"something is drawn over their face (up to {worst.covered:.0%} of it at {worst.t:.2f} s)"
        out.append(f"MEASURED at {_times(covered)}: {how}. Their face stays clear: route it behind them (inside "
                   f".behind / further from the camera) or away from their head.")
    for edge in ("left", "right", "top"):
        cut = [s for s in seen if edge in s.cut]
        if cut:
            out.append(f"MEASURED at {_times(cut)}: they are cut off at the {edge} edge of the frame, which their own "
                       f"shot isn't: move them (or the camera) so all of them that their shot shows stays in frame.")
    small = [s for s in seen if s.size < SMALL_AREA]
    if small:
        least = min(small, key=lambda s: s.size)
        out.append(f"MEASURED at {_times(small)}: they are shown at about {least.size ** 0.5:.0%} of their own size. "
                   f"They stay comfortably big (their place in the bible, scale about .8 or more).")
    return out


def _times(seen: list[Seen]) -> str:
    ts = [f"{s.t:.1f}" for s in seen[:6]]
    return ", ".join(ts) + (" s" if len(seen) <= 6 else f" s and {len(seen) - 6} more")


def sample_times(duration: float, every: float = 1.5, most: int = 16) -> list[float]:
    n = max(2, min(most, int(duration / every)))
    return [round(duration * (k + 0.5) / n, 2) for k in range(n)]


def source_masks(matte, times: list[float]) -> dict[float, object]:
    """Their cut-out in their own shot at these times (section seconds), from the stage's clean matte video."""
    import cv2

    out = {}
    cap = cv2.VideoCapture(str(matte))
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if ok:
            out[t] = frame[..., 0] if frame.ndim == 3 else frame
    cap.release()
    return out
