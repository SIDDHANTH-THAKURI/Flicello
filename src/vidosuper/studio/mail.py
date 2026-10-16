"""Email: sign-in codes and account notices, sent through Resend (RESEND_API_KEY in the environment).

Without a key nothing is sent: `available()` is False and the sign-in page offers Google and passkeys only. For local
tests `mail_outbox` writes each email to <home>/outbox/ instead. Emails never carry footage, prompts or tokens other
than the one-time code they exist for.
"""

from __future__ import annotations

import html
import json
import logging
import os
import time
from pathlib import Path

from .settings import StudioSettings

log = logging.getLogger("vidosuper.studio.mail")
RESEND = "https://api.resend.com/emails"


class MailError(RuntimeError):
    """The email could not be sent (shown to the person as a plain retry message)."""


def available(s: StudioSettings) -> bool:
    return bool(s.mail_outbox or os.environ.get("RESEND_API_KEY"))


def send(s: StudioSettings, to: str, subject: str, html_body: str, text_body: str) -> None:
    if s.mail_outbox:
        box = s.home / "outbox"
        box.mkdir(parents=True, exist_ok=True)
        name = f"{time.time():.3f}-{to.replace('@', '_at_')}.json"
        (box / name).write_text(json.dumps({"to": to, "subject": subject, "text": text_body, "html": html_body}),
                                encoding="utf-8")
        return
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise MailError("Email is not set up yet.")
    import httpx

    try:
        r = httpx.post(RESEND, timeout=12, headers={"Authorization": f"Bearer {key}"},
                       json={"from": s.mail_from, "to": [to], "subject": subject, "html": html_body, "text": text_body})
    except httpx.HTTPError as exc:
        log.warning("mail: %s", exc.__class__.__name__)
        raise MailError("We couldn't send the email. Please try again in a minute.") from exc
    if r.status_code >= 300:
        log.warning("mail: status %s", r.status_code)  # never log the address or the body
        raise MailError("We couldn't send the email. Please try again in a minute.")


def _frame(s: StudioSettings, title: str, inner: str) -> str:
    """The shared email frame: dark card, the logo, plain footer. Inline styles only (email clients)."""
    site = (s.site_url or "https://flicello.com").rstrip("/")
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#06060a;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#06060a;padding:40px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#111119;border:1px solid #23232e;border-radius:24px;">
<tr><td style="padding:36px 36px 8px 36px;">
<img src="{site}/static/icon-192.png" width="40" height="40" alt="Flicello" style="display:block;border-radius:11px;">
<p style="margin:26px 0 0 0;font:700 24px/1.25 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#f4f1ea;letter-spacing:-0.4px;">{html.escape(title)}</p>
</td></tr>
<tr><td style="padding:12px 36px 32px 36px;font:16px/1.6 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#d9d5cc;">{inner}</td></tr>
</table>
<p style="margin:22px 0 0 0;font:12px/1.6 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#66636f;">
Flicello · Siddhanth Thakuri, trading as Flicello · ABN 25 443 793 186 · NSW, Australia<br>
<a href="{site}/legal/privacy" style="color:#9b98a4;">Privacy</a> · <a href="{site}/legal/terms" style="color:#9b98a4;">Terms</a></p>
</td></tr></table></body></html>"""


def code_email(s: StudioSettings, code: str, minutes: int) -> tuple[str, str, str]:
    spaced = " ".join(code)
    inner = f"""<p style="margin:0 0 22px 0;">Here is your sign-in code. It works once, for the next {minutes} minutes.</p>
<div style="margin:0 0 24px 0;padding:22px 0;border-radius:18px;background:#0b0b11;border:1px solid #2a2a36;text-align:center;
font:700 36px/1 'SFMono-Regular',Consolas,'Liberation Mono',monospace;letter-spacing:10px;color:#ffffff;">{spaced}</div>
<p style="margin:0;color:#9b98a4;font-size:14px;">If you didn't ask for this, you can ignore this email: nobody can sign in without the code.
We will never ask you for this code by phone or message.</p>"""
    text = (f"Your Flicello sign-in code: {code}\n\nIt works once, for the next {minutes} minutes.\n"
            "If you didn't ask for this, ignore this email: nobody can sign in without the code.")
    return f"{code} is your Flicello code", _frame(s, "Your sign-in code", inner), text


def notice_email(s: StudioSettings, title: str, lines: list[str]) -> tuple[str, str, str]:
    inner = "".join(f'<p style="margin:0 0 14px 0;">{html.escape(line)}</p>' for line in lines)
    return title, _frame(s, title, inner), "\n\n".join(lines)


def outbox(s: StudioSettings) -> list[dict]:
    """Emails written in outbox mode, newest last (tests and local runs)."""
    box = Path(s.home) / "outbox"
    if not box.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(box.glob("*.json"))]
