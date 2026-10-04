"""Music sources (licence filtering), automatic selection, re-sync to a new track, and the auto-music
run flow. Network calls are mocked; the 'downloads' are locally synthesised tracks."""

from __future__ import annotations

import json

import numpy as np
import pytest

from vidosuper.devtools import make_placeholder_music
from vidosuper.edit.resync import resync_edl
from vidosuper.edit.snap import timeline_grid
from vidosuper.edit.timeline import build_timeline
from vidosuper.music import analyze_music, sources
from vidosuper.music import select as select_module
from vidosuper.music.select import find_music
from vidosuper.music.sources import JamendoProvider, MusicQuery, OpenverseProvider, TrackCandidate, license_from_url
from vidosuper.schemas import EDL, ClipInfo, EditMeta, MusicBrief, MusicSpec, Segment


def test_openverse_keeps_only_safe_licences(monkeypatch):
    fake = {"results": [
        {"id": "1", "title": "Good", "creator": "A", "license": "by", "duration": 180000, "url": "https://x/1.mp3",
         "attribution": "Good by A, CC BY"},
        {"id": "2", "title": "ShareAlike", "creator": "B", "license": "by-sa", "duration": 180000, "url": "https://x/2.mp3"},
        {"id": "3", "title": "Too short", "creator": "C", "license": "cc0", "duration": 20000, "url": "https://x/3.mp3"},
    ]}
    monkeypatch.setattr(sources, "_get_json", lambda url, cache: fake)
    found = OpenverseProvider().search(MusicQuery(keywords=["calm"], min_duration=60))
    assert [c.title for c in found] == ["Good"] and found[0].license == "by"


def test_jamendo_parsing_and_licence_filter(monkeypatch):
    fake = {"headers": {"status": "success"}, "results": [
        {"id": "10", "name": "Drift", "artist_name": "X", "duration": 200, "license_ccurl":
         "http://creativecommons.org/licenses/by/3.0/", "audiodownload_allowed": True, "audiodownload": "https://j/10",
         "audio": "https://s/10", "shareurl": "https://jamendo/10",
         "musicinfo": {"vocalinstrumental": "instrumental", "tags": {"genres": ["ambient"], "vartags": ["calm"]}}},
        {"id": "11", "name": "NoCommercial", "artist_name": "Y", "duration": 200, "license_ccurl":
         "http://creativecommons.org/licenses/by-nc-sa/3.0/", "audiodownload_allowed": True, "audiodownload": "https://j/11"},
        {"id": "12", "name": "StreamOnly", "artist_name": "Z", "duration": 200, "license_ccurl":
         "http://creativecommons.org/licenses/by/4.0/", "audiodownload_allowed": False, "audio": "https://s/12"},
    ]}
    monkeypatch.setattr(sources, "_get_json", lambda url, cache: fake)
    found = JamendoProvider("key").search(MusicQuery(keywords=["calm"]))
    assert [c.title for c in found] == ["Drift", "StreamOnly"]
    assert found[0].download_url == "https://j/10" and found[1].download_url == "https://s/12"
    assert "CC BY 3.0" in found[0].attribution and found[0].instrumental is True
    assert license_from_url("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0"


@pytest.fixture(scope="module")
def tracks(tmp_path_factory):
    root = tmp_path_factory.mktemp("tracks")
    return {bpm: make_placeholder_music(root / f"t{bpm}.wav", bpm=bpm) for bpm in (90, 128)}


class _FakeProvider:
    name = "fake"

    def __init__(self, files):
        self.files = files

    def search(self, query):
        return [TrackCandidate(provider="fake", id=str(bpm), title=f"Track {bpm} chill", artist="Synth", duration=70,
                               license="cc0", attribution=f"Track {bpm} (CC0)", download_url=str(path), tags=["chill"])
                for bpm, path in self.files.items()]


def test_selection_prefers_the_track_that_fits_the_brief(tracks, tmp_path, monkeypatch):
    monkeypatch.setattr(select_module, "download", lambda cand, library: tracks[int(cand.id)])
    brief = MusicBrief(mood=["chill"], genres=["electronic"], bpm_min=120, bpm_max=135,
                       energy_arc=[0.3, 0.5, 0.9, 0.9, 0.4, 0.9, 0.2])
    scored = find_music(brief, 40, [_FakeProvider(tracks)], tmp_path / "lib", tmp_path / "cache", log=lambda m: None)
    assert scored[0].candidate.id == "128"
    assert scored[0].offset in scored[0].analysis.downbeats  # starts on a bar


def _clip(cid, duration=60.0):
    return ClipInfo(clip_id=cid, path=cid, rel_path=cid, fingerprint=cid, duration=duration, width=1920, height=1080,
                    fps=60, has_audio=True)


def test_resync_keeps_cuts_on_the_same_beats(tracks):
    slow = analyze_music(tracks[90])
    fast = analyze_music(tracks[128])
    clips = {"a": _clip("a"), "b": _clip("b")}
    beat_slow = 60 / 90
    segs = [Segment(id=f"s{k}", clip_id="a" if k % 2 else "b", src_in=1.0 + k, src_out=1.0 + k + 4 * beat_slow)
            for k in range(5)]  # every shot is exactly 4 beats at 90 BPM
    edl = EDL(project_dir=".", meta=EditMeta(fps=30), segments=segs,
              music=MusicSpec(path=str(tracks[90]), start=slow.downbeats[0]))
    resync_edl(edl, clips, slow, fast, str(tracks[128]), fast.downbeats[0])
    grid, _ = timeline_grid(edl.music, fast, 40)
    cuts = [it.start / 30 for it in build_timeline(edl, clips).items[1:]]
    beat_index = [int(np.argmin(np.abs(grid - c))) for c in cuts]
    assert beat_index == [4, 8, 12, 16]  # still every 4 beats, now at 128 BPM
    assert max(min(abs(grid - c)) for c in cuts) < 1.5 / 30


def test_auto_music_run_flow(ingested, cfg, tracks, monkeypatch):
    from vidosuper.loop import find_music_for_run, start_run, submit_brief
    from vidosuper.perceive.prep import analysis_path

    project, clips, _ = ingested
    for clip in clips.values():  # every clip needs an analysis before a run can start
        path = analysis_path(project, clip.clip_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"clip_id": clip.clip_id, "summary": "s", "scene": "s", "camera": "static",
                                        "energy": 2, "visual_appeal": 3, "moments": [{
                                            "start": 0.5, "end": 2.0, "kind": "detail", "description": "d",
                                            "hook": 2, "strength": 2, "best_use": "b-roll"}]}), encoding="utf-8")
    run = start_run(project, cfg, intent="test", platform="youtube", target_seconds=8, music_mode="auto")
    brief = {"title": "T", "logline": "L", "target_seconds": 8, "hook": "h", "ending": "e", "thesis": "t",
             "dynamics": [0.9, 0.2, 0.6, 0.8], "convention_break": "c",
             "music_brief": {"mood": ["chill"], "genres": ["electronic"], "bpm_min": 120, "bpm_max": 135},
             "structure": [{"name": "all", "purpose": "p", "start": 0, "end": 8, "energy": 3, "shot_length": "x",
                            "moments": ["c01#1"]}]}
    run.brief_path.write_text(json.dumps(brief), encoding="utf-8")
    assert submit_brief(run, project) == [] and run.load().stage == "music"
    monkeypatch.setattr("vidosuper.music.sources.providers_from_env", lambda cache, prefer: [_FakeProvider(tracks)])
    monkeypatch.setattr(select_module, "download", lambda cand, library: tracks[int(cand.id)])
    find_music_for_run(run, project, cfg, log=lambda m: None)
    state = run.load()
    assert state.stage == "edit" and state.music == str(tracks[128].resolve()) and state.music_start_hint is not None
    assert "Suggested music.start" in run.music_md.read_text(encoding="utf-8")
    assert (run.dir / "music_candidates.md").exists()
