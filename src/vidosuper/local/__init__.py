"""Flicello on your own machine: everything it needs, checked and connected.

Flicello runs locally, on the person's own computer, with their own Claude Code plan and their own keys. This package
is how it knows what it has:

- `machine`: the computer itself (memory, graphics, disk) against what Flicello is built for. It is made for high-end
  machines and says so plainly; it never cuts quality for a weak one.
- `keys`: the person's keys (Gemini, ElevenLabs, optional Anthropic and Higgsfield), kept in the operating system's
  keychain on this machine only, and put into the environment of the running app.
- `checks`: every tool, model and service it uses, each ready / missing / failing, with what to do.
- `health`: what went wrong with a service while working (credit out, key refused, rate limited), remembered so the
  Connect page and Cello can say it plainly.
- `setup_prompt`: the prompt the person pastes into Claude Code so it installs or fixes whatever is missing here.
"""


def claude_exe() -> str:
    """Claude Code's own program, found on the PATH: on Windows an npm install is `claude.cmd`, which a bare
    "claude" doesn't start."""
    import shutil

    return shutil.which("claude") or "claude"


def run_claude(cmd: list[str], prompt: str, folder, **kw):
    """`claude -p` with its prompt read from a file in `folder` as its standard input. A pipe needs a thread to feed
    it, and a process busy elsewhere (a model loading in another thread) can hold that thread up past the 3 s Claude
    Code waits for its input; a file is simply there. Returns the CompletedProcess (stdout and stderr captured)."""
    import secrets
    import subprocess
    from pathlib import Path

    path = Path(folder) / f".prompt-{secrets.token_hex(4)}.txt"
    path.write_text(prompt, encoding="utf-8")
    try:
        with open(path, "rb") as given:
            return subprocess.run(cmd, stdin=given, capture_output=True, **kw)
    finally:
        path.unlink(missing_ok=True)


def plan_env() -> dict:
    """The environment for running Claude Code on the person's own plan. Claude Code bills an API key instead of
    the plan whenever ANTHROPIC_API_KEY is in its environment, so it is taken out (with its auth-token cousins)."""
    import os

    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(name, None)
    return env
