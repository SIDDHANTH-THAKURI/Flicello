"""The person's keys, kept on this machine only: in the operating system's keychain (Windows Credential Manager,
macOS Keychain, the Secret Service on Linux), else in a file only this user can read. Never in the repository, never
sent anywhere but to the service the key belongs to. At start they are put into this process's environment, where
every part of Flicello already looks for them."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVICE = "flicello"


@dataclass(frozen=True)
class Key:
    name: str  # the environment variable
    label: str
    does: str  # what it powers, in a few words
    get: str  # where to make one
    needed: bool  # Flicello is much less without it
    secret: bool = True


KEYS = (
    Key("GEMINI_API_KEY", "Gemini", "Cello's live voice, watching your footage, painting assets",
        "https://aistudio.google.com/apikey", True),
    Key("ELEVENLABS_API_KEY", "ElevenLabs", "music made for your film, sound effects, voices",
        "https://elevenlabs.io/app/settings/api-keys", True),
    Key("ANTHROPIC_API_KEY", "Anthropic API (optional)", "only if you'd rather pay per use than use your Claude Code plan",
        "https://console.anthropic.com/settings/keys", False),
    Key("HF_API_KEY_ID", "Higgsfield key id (optional)", "AI video generation", "https://higgsfield.ai", False),
    Key("HF_API_KEY_SECRET", "Higgsfield secret (optional)", "AI video generation", "https://higgsfield.ai", False),
)
NAMES = {k.name for k in KEYS}


def _file(home: Path) -> Path:
    return home / "keys.env"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - no keychain here: the private file is used
        return None


def get(name: str, home: Path) -> str:
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE, name)
            if value:
                return value
        except Exception:  # noqa: BLE001
            pass
    path = _file(home)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip()
    return ""


def save(name: str, value: str, home: Path) -> str:
    """Keep a key (an empty value forgets it); returns where it went ("keychain" or "file")."""
    if name not in NAMES:
        raise ValueError(f"{name} is not a key Flicello uses")
    value = value.strip()
    kr = _keyring()
    where = "file"
    if kr is not None:
        try:
            if value:
                kr.set_password(SERVICE, name, value)
            else:
                try:
                    kr.delete_password(SERVICE, name)
                except Exception:  # noqa: BLE001 - it wasn't there
                    pass
            where = "keychain"
        except Exception:  # noqa: BLE001 - the keychain refused: the private file
            kr = None
    if kr is None:
        path = _file(home)
        lines = [ln for ln in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
                 if ln.partition("=")[0].strip() != name]
        if value:
            lines.append(f"{name}={value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    return where


def load(home: Path) -> list[str]:
    """Put the kept keys into this process's environment (a key already set there wins). Returns the names set."""
    done = []
    for k in KEYS:
        if os.environ.get(k.name):
            continue
        value = get(k.name, home)
        if value:
            os.environ[k.name] = value
            done.append(k.name)
    return done


def masked(value: str) -> str:
    return "" if not value else (value[:4] + "…" + value[-4:] if len(value) > 12 else "set")
