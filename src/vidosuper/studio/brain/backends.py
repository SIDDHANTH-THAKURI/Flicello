"""How a question reaches Claude and comes back as checked JSON.

Two backends share one call, `ask(request) -> (data, usage)`:
- ApiBackend: the Claude API with the studio's key; structured output (JSON schema), falling back to a
  forced tool call if the API refuses the schema; the system prompt and the footage are prompt-cached so a
  retry costs little.
- ClaudeCodeBackend: headless `claude -p` on the owner's own Claude plan (their videos only). No tools but
  Read (for the contact sheets), no settings or MCP servers loaded, a custom system prompt, a budget cap.

`structured()` sends a request, validates the answer with its pydantic model plus a task-specific check,
and on problems asks again with the problems listed (twice at most). Every call's usage goes to `spend`,
which records it and raises BudgetExceeded when a cap would be crossed.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..settings import price_for


class BrainError(RuntimeError):
    """The brain could not produce a usable answer (after retries)."""


class BudgetExceeded(BrainError):
    """An AI call would cross a spending cap; the job waits instead."""


class PersonLimit(BudgetExceeded):
    """One person's spending in the last 24 hours reached their daily share: their step carries on by itself once
    enough of it is more than a day old (`resume_at`)."""

    def __init__(self, message: str, resume_at: float):
        super().__init__(message)
        self.resume_at = resume_at


@dataclass
class Usage:
    task: str
    model: str
    backend: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    usd: float = 0.0
    billed: bool = True  # real API money; False when it ran on the owner's Claude plan (usd is the API equivalent)
    seconds: float = 0.0

    @staticmethod
    def priced(task: str, model: str, backend: str, inp: int, out: int, cw: int, cr: int, seconds: float,
               billed: bool = True) -> "Usage":
        p_in, p_out, p_cw, p_cr = price_for(model)
        usd = (inp * p_in + out * p_out + cw * p_cw + cr * p_cr) / 1e6
        return Usage(task, model, backend, inp, out, cw, cr, round(usd, 5), billed, round(seconds, 2))


@dataclass
class Request:
    task: str  # analyse / pitch / design / revise / learn
    system: str
    text: str
    schema: type[BaseModel]
    model: str
    images: list[Path] = field(default_factory=list)
    effort: str = "medium"
    max_tokens: int = 8000


SpendFn = Callable[[Usage], None]
CheckFn = Callable[[BaseModel], list[str]]


# ---------------------------------------------------------------- schema for the model

_DROP_KEYS = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
              "maxLength", "minItems", "maxItems", "pattern", "examples"}


def model_schema(schema: type[BaseModel]) -> dict:
    """The model's JSON schema, simplified for structured output: definitions kept, every object closed
    (additionalProperties false) with all its properties required; range checks stay in pydantic."""
    raw = schema.model_json_schema()

    def clean(node, names: bool = False):
        if isinstance(node, dict):
            if names:  # the keys of `properties` / `$defs` are field and type names, not schema keywords
                return {k: clean(v) for k, v in node.items()}
            out = {k: clean(v, names=k in ("properties", "$defs", "definitions"))
                   for k, v in node.items() if k not in _DROP_KEYS}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(copy.deepcopy(raw))


def describe_errors(exc: ValidationError, limit: int = 12) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:limit]]


def _json_from_text(text: str) -> dict:
    """The first JSON object in a reply (tolerates a fenced block or a sentence around it)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


# ---------------------------------------------------------------- the Claude API

class ApiBackend:
    name = "api"

    def __init__(self, api_key: str | None = None, timeout: float = 600.0):
        import anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise BrainError("No ANTHROPIC_API_KEY: add it to the .env file (never paste it in chat).")
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=timeout)
        self._variant: dict[tuple[str, str], int] = {}  # (model, schema) -> request variant that the API accepts

    def _content(self, req: Request, correction: str | None) -> list[dict]:
        blocks: list[dict] = [{"type": "text", "text": req.text}]
        for path in req.images:
            data = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
            blocks.append({"type": "text", "text": f"Image: {Path(path).name}"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        blocks[-1]["cache_control"] = {"type": "ephemeral"}  # a retry re-reads all of this from the cache
        if correction:
            blocks.append({"type": "text", "text": correction})
        return blocks

    def ask(self, req: Request, correction: str | None = None) -> tuple[dict, Usage]:
        schema = model_schema(req.schema)
        base = dict(model=req.model, max_tokens=req.max_tokens,
                    system=[{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": self._content(req, correction)}])
        variants = [
            lambda: dict(output_config={"format": {"type": "json_schema", "schema": schema}, "effort": req.effort}),
            lambda: dict(output_config={"format": {"type": "json_schema", "schema": schema}}),
            lambda: dict(tools=[{"name": "submit", "description": f"Submit the {req.task} result.", "input_schema": schema}],
                         tool_choice={"type": "tool", "name": "submit"}),
        ]
        key = (req.model, req.schema.__name__)
        first = self._variant.get(key, 0)
        t0 = time.monotonic()
        last_error: Exception | None = None
        for i in range(first, len(variants)):
            try:
                msg = self.client.messages.create(**base, **variants[i]())
            except self.anthropic.BadRequestError as exc:  # this model or schema doesn't take that form: simpler one
                last_error = exc
                continue
            self._variant[key] = i
            if i < 2:
                text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
                data = _json_from_text(text)
            else:
                data = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
                if data is None:
                    raise BrainError(f"{req.task}: the model did not return a result")
            u = msg.usage
            usage = Usage.priced(req.task, req.model, self.name, u.input_tokens or 0, u.output_tokens or 0,
                                 u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0,
                                 time.monotonic() - t0)
            return data, usage
        raise BrainError(f"{req.task}: the API refused every request form: {last_error}")


# ---------------------------------------------------------------- headless Claude Code (owner only)

class ClaudeCodeBackend:
    """`claude -p` with the owner's login. Never used for other people's videos (see core.brain_for)."""

    name = "claude_code"

    def __init__(self, exe: str = "claude", timeout: float = 900.0, cap_usd: float = 3.0):
        self.exe = exe
        self.timeout = timeout
        self.cap_usd = cap_usd

    def ask(self, req: Request, correction: str | None = None) -> tuple[dict, Usage]:
        prompt = req.text
        if req.images:
            prompt += ("\n\nLook at every one of these images with the Read tool, in this order, before you "
                       "answer:\n" + "\n".join(f"- {Path(p).resolve()}" for p in req.images))
        if correction:
            prompt += "\n\n" + correction
        with tempfile.TemporaryDirectory(prefix="vido-brain-") as tmp:
            system_file = Path(tmp) / "system.md"
            system_file.write_text(req.system, encoding="utf-8")
            cmd = [self.exe, "-p", "--output-format", "json", "--model", req.model,
                   "--json-schema", json.dumps(model_schema(req.schema)),
                   "--system-prompt-file", str(system_file), "--no-session-persistence",
                   "--setting-sources", "", "--strict-mcp-config", "--max-budget-usd", f"{self.cap_usd:.2f}",
                   "--tools", "Read" if req.images else ""]
            cwd = Path(req.images[0]).resolve().parent if req.images else Path(tmp)
            for folder in sorted({str(Path(p).resolve().parent) for p in req.images}):
                cmd += ["--add-dir", folder]
            t0 = time.monotonic()
            try:
                proc = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=cwd,
                                      timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise BrainError(f"{req.task}: Claude Code took longer than {self.timeout:.0f}s") from exc
        out = proc.stdout.decode("utf-8", errors="replace")
        try:
            result = json.loads(out)
        except json.JSONDecodeError as exc:
            err = proc.stderr.decode("utf-8", errors="replace")[-600:]
            raise BrainError(f"{req.task}: Claude Code gave no result (exit {proc.returncode}): {err or out[-600:]}") from exc
        if result.get("is_error") or result.get("subtype") not in (None, "success"):
            raise BrainError(f"{req.task}: Claude Code stopped: {result.get('subtype')} {str(result.get('result'))[:300]}")
        data = result.get("structured_output")
        if not isinstance(data, dict):
            data = _json_from_text(str(result.get("result") or ""))
        u = result.get("usage") or {}
        usage = Usage.priced(req.task, req.model, self.name, int(u.get("input_tokens") or 0),
                             int(u.get("output_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
                             int(u.get("cache_read_input_tokens") or 0), time.monotonic() - t0, billed=False)
        if result.get("total_cost_usd") is not None:  # Claude Code's own API-equivalent figure
            usage.usd = round(float(result["total_cost_usd"]), 5)
        return data, usage


# ---------------------------------------------------------------- checked answers

def structured(backend, req: Request, spend: SpendFn, check: CheckFn | None = None, retries: int = 2,
               log: Callable[[str, dict], None] | None = None):
    """Ask, validate, and ask again with the problems (at most `retries` more times)."""
    correction = None
    problems: list[str] = []
    for attempt in range(retries + 1):
        data, usage = backend.ask(req, correction)
        spend(usage)
        if log:
            log(f"{req.task}-{attempt + 1}", data)
        try:
            obj = req.schema.model_validate(data)
        except ValidationError as exc:
            problems = describe_errors(exc)
        else:
            problems = check(obj) if check else []
            if not problems:
                return obj
        correction = ("Your previous answer had these problems. Fix all of them and return the complete "
                      "corrected JSON:\n- " + "\n- ".join(problems[:20]) +
                      "\n\nYour previous answer was:\n" + json.dumps(data)[:20000])
    raise BrainError(f"{req.task}: still not valid after {retries + 1} tries: " + "; ".join(problems[:6]))
