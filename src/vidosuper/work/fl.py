"""`bash fl ...`: the work agent's hands on Flicello (run from its folder; see brief.md).

It talks to the running Flicello on this computer with the session's own token (FLICELLO_AGENT), so everything it
does goes through the same checks as the person's own buttons and shows on their screen as it happens."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

URL = os.environ.get("FLICELLO_URL", "http://127.0.0.1:8765")
TOKEN = os.environ.get("FLICELLO_AGENT", "")



def call(method: str, path: str, body: dict | None = None, timeout: float = 120.0) -> dict:
    import httpx

    r = httpx.request(method, URL + path, json=body, headers={"X-Flicello-Agent": TOKEN}, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = {"error": r.text[:500]}
    if r.status_code >= 400:
        raise SystemExit(f"Flicello said {r.status_code}: {data.get('error') or data}")
    return data


def show(data) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False) if not isinstance(data, str) else data)


def tool(name: str, args: dict) -> dict:
    return call("POST", "/api/cello/act", {"name": name, "args": args}, timeout=900)  # the session's own film


def wait(path: str, timeout: float) -> dict:
    """Polls a running step (or scene task) until it ends or the time is up."""
    deadline = time.time() + min(590.0, max(5.0, timeout))
    while True:
        out = call("GET", path)
        if out.get("done") or time.time() > deadline:
            if not out.get("done"):
                out["note"] = "still going: wait again"
            return out
        time.sleep(3)


def look_at(times: list[float], what: str) -> None:
    """Stills of what just changed, made at once, so the work is looked at before it is called done."""
    times = sorted({round(t, 2) for t in times if t >= 0})
    if len(times) > 6:  # spread over the change
        times = [times[round(k * (len(times) - 1) / 5)] for k in range(6)]
    if not times:
        return
    try:
        out = call("GET", f"/api/work/frames?t={','.join(f'{x:g}' for x in times)}", timeout=600)
    except SystemExit as exc:
        print(f"(No stills to look at: {exc})")
        return
    print(f"\n{what}: Read each one and look before you say it's right:")
    for f in out["frames"]:
        print(f"- {f['t']:.2f} s: {f['file']}")


def shot_times(shots: list[dict], numbers: list[int]) -> list[float]:
    """The middle of each of these shots (numbered as on their screen)."""
    by_n = {s["n"]: s for s in shots}
    return [(by_n[n]["from"] + by_n[n]["to"]) / 2 for n in numbers if n in by_n]


def changed_shots(before: dict, after: dict) -> list[int]:
    """The shots (numbered from 1) of a rewritten plan that differ from the plan it was made from, and the shots the
    graphics that changed come in on."""
    def shot(s: dict, pool: dict) -> tuple:
        return (pool.get(s.get("key")), s.get("beats"), s.get("kind"), s.get("count"), s.get("transition"),
                s.get("src_in"), s.get("zoom"), s.get("frame_x"), s.get("flip"))

    pool_before = {m["key"]: (m["clip"], m["start"], m["end"]) for m in before.get("pool") or []}
    pool_after = {m["key"]: (m["clip"], m["start"], m["end"]) for m in after.get("pool") or []}
    old = [shot(s, pool_before) for s in before.get("plan") or []]
    out = [i + 1 for i, s in enumerate(after.get("plan") or []) if i >= len(old) or old[i] != shot(s, pool_after)]
    was = {json.dumps(g, sort_keys=True) for g in before.get("graphics") or []}
    out += [max(1, int(g.get("shot") or 1)) for g in after.get("graphics") or [] if json.dumps(g, sort_keys=True) not in was]
    return sorted(set(out))


def fields_text() -> str:
    """What every field of a plan means: the plan, its pool moments, its shots and its graphics."""
    from vidosuper.studio.brain.schemas import DesignPlan, PlanGraphic, PlanShot, PoolMoment

    out = []
    for model in (DesignPlan, PoolMoment, PlanShot, PlanGraphic):
        out.append(f"\n{model.__name__}:")
        for name, f in model.model_fields.items():
            kind = re.sub(r"<class '([^']+)'>", r"\1", str(f.annotation)).replace("typing.", "")
            kind = re.sub(r"\b[a-z_]+(?:\.[a-z_]+)+\.(?=[A-Z])", "", kind)  # PoolMoment, not vidosuper....PoolMoment
            default = "" if f.is_required() else f", default {f.get_default(call_default_factory=True)!r}"
            out.append(f"- {name} ({kind}{default}): {' '.join((f.description or '').split())}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="fl", description="Flicello at your hands.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("help", help="every command, and every tool with its arguments")
    sub.add_parser("state", help="the screen and the open film")
    t = sub.add_parser("tool", help="run one of Cello's tools")
    t.add_argument("name")
    t.add_argument("args", nargs="?", default="{}", help="its arguments as JSON")
    pr = sub.add_parser("progress", help="what you're doing now (shown beside Cello)")
    pr.add_argument("text", nargs="+")
    ch = sub.add_parser("choice", help="a lasting choice of theirs, kept visible")
    ch.add_argument("what")
    ch.add_argument("value", nargs="+")
    sc = sub.add_parser("scene", help="design a motion-graphics scene into the film (returns a task id)")
    sc.add_argument("what", nargs="+")
    sc.add_argument("--from", dest="start", type=float, required=True, help="film seconds it starts")
    sc.add_argument("--to", dest="end", type=float, required=True, help="film seconds it ends")
    sc.add_argument("--style", default="")
    ct = sub.add_parser("cartoon", help="make a cartoon from a story, as a new film (returns a task id)")
    ct.add_argument("story", nargs="+", help="the story, in their words or yours")
    ct.add_argument("--seconds", type=float, default=40.0, help="about how long (8 to 300)")
    ct.add_argument("--shape", default="9:16", choices=["9:16", "16:9", "1:1", "4:5"])
    ct.add_argument("--style", default="", help="the look they asked for, if any")
    ct.add_argument("--voice", default="", help="a clip of the open film whose sound is their narration")
    w = sub.add_parser("wait", help="wait for the film's running step (job), a scene task (scene <id>) or a cartoon "
                                    "(cartoon <id>)")
    w.add_argument("what", choices=["job", "scene", "cartoon"])
    w.add_argument("id", nargs="?", default="")
    w.add_argument("--timeout", type=float, default=540.0, help="seconds (at most 590)")
    op = sub.add_parser("open", help="work on another film (its id or title)")
    op.add_argument("film", nargs="+")
    fr = sub.add_parser("frames", help="stills of what plays at these film seconds (the draft on screen as their "
                                       "player shows it, else the version; or --version N): Read them to look")
    fr.add_argument("times", nargs="+", type=float)
    fr.add_argument("--version", default="", help="a version's number (default: what is on their screen)")
    pl = sub.add_parser("plan", help="the whole edit as one plan to rewrite: get (plan.json, moments.json), fields "
                                     "(what each field means), set (checked, then a draft on their screen)")
    pl.add_argument("action", nargs="?", choices=["get", "fields", "set"], default="get")
    pl.add_argument("file", nargs="?", default="plan.json")
    pl.add_argument("--said", action="append", default=[], help="set: one plain line per change, as they'll read it")
    a = p.parse_args(argv)

    if a.cmd == "help":
        p.print_help()
        print("\nCello's tools (bash fl tool <name> '<json>'):")
        for t_ in call("GET", "/api/work/tools")["tools"]:
            props = ", ".join(f"{k}: {v.get('type', '')}" for k, v in (t_.get("parameters", {}).get("properties") or {}).items())
            print(f"- {t_['name']}({props}): {t_['description']}")
        return
    if a.cmd == "state":
        show(tool("look", {}).get("result"))
        return
    if a.cmd == "tool":
        try:
            args = json.loads(a.args)
        except ValueError as exc:
            raise SystemExit(f"The arguments must be JSON: {exc}")
        out = tool(a.name, args)
        show(out)
        made = a.name == "timeline" and str(out.get("result", "")).startswith("Made")  # a draft made at once
        if (out.get("ui") or {}).get("wait") == "draft":  # a draft on its way: wait for it
            done = wait("/api/work/job", 300)
            show(done)
            made = (done.get("last") or {}).get("status") == "done"
        if made:  # then look at it
            shots = (tool("look", {}).get("result") or {}).get("shots") or []
            named = sorted({int(o.get("shot") or 1) for o in args.get("ops") or [] if isinstance(o, dict) and o.get("shot")})
            spread = [s["n"] for s in shots[:: max(1, len(shots) // 4)]][:5]
            look_at(shot_times(shots, named or spread), "Stills of the draft where it changed")
        return
    if a.cmd == "progress":
        call("POST", "/api/work/progress", {"text": " ".join(a.text)})
        print("ok")
        return
    if a.cmd == "choice":
        call("POST", "/api/work/choice", {"what": a.what, "value": " ".join(a.value)})
        print("kept")
        return
    if a.cmd == "open":
        out = call("POST", "/api/work/open", {"film": " ".join(a.film)})
        show(out)
        return
    if a.cmd == "scene":
        out = call("POST", "/api/work/scene", {"what": " ".join(a.what), "start": a.start, "end": a.end,
                                               "style": a.style})
        show(out)
        return
    if a.cmd == "cartoon":
        out = call("POST", "/api/work/cartoon", {"story": " ".join(a.story), "seconds": a.seconds, "shape": a.shape,
                                                 "style": a.style, "voice": a.voice})
        show(out)
        return
    if a.cmd == "wait":
        path = {"scene": f"/api/work/scene/{a.id}", "cartoon": f"/api/work/cartoon/{a.id}"}.get(a.what, "/api/work/job")
        show(wait(path, a.timeout))
        return
    if a.cmd == "frames":
        out = call("GET", f"/api/work/frames?t={','.join(f'{x:g}' for x in a.times)}&which={a.version}", timeout=600)
        print(f"Stills of {'the draft on their screen' if out['which'] == 'draft' else 'version ' + out['which']}:")
        for f in out["frames"]:
            print(f"- {f['t']:.2f} s: {f['file']}")
        print("Read them (the Read tool shows images) and look before you say it's right.")
        return
    if a.cmd == "plan":
        if a.action == "fields":
            print(fields_text())
            return
        if a.action == "get":
            out = call("GET", "/api/work/plan")
            Path(a.file).write_text(json.dumps(out["plan"], indent=1, ensure_ascii=False), encoding="utf-8")
            Path(".plan-before.json").write_text(json.dumps(out["plan"]), encoding="utf-8")  # to see what changes
            Path("moments.json").write_text(json.dumps({"clips": out["clips"], "moments": out["moments"]}, indent=1,
                                                       ensure_ascii=False), encoding="utf-8")
            print("\n".join([*out["about"], f"Written: {a.file} (the plan) and moments.json ({len(out['moments'])} "
                              "moments of their footage, by clip). What each field means: bash fl plan fields.",
                              f"To make it: edit {a.file}, then bash fl plan set {a.file} --said \"<a plain line per "
                              "change>\" (checked, then a draft on their screen)."]))
            return
        try:
            plan = json.loads(Path(a.file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Can't read {a.file}: {exc}")
        out = call("POST", "/api/work/plan", {"plan": plan, "said": a.said})
        print(out.get("note", ""))
        done = wait("/api/work/job", 300)
        show(done)
        if (done.get("last") or {}).get("status") == "done":
            try:
                before = json.loads(Path(".plan-before.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                before = {}
            shots = (tool("look", {}).get("result") or {}).get("shots") or []
            look_at(shot_times(shots, changed_shots(before, plan)), "Stills of what changed")
        return


if __name__ == "__main__":
    main(sys.argv[1:])
