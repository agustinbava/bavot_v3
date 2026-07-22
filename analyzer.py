"""Anthropic analysis: send a payload, get the scalping verdict back."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Verdict:
    """Parsed result of one instrument analysis."""

    symbol: str
    verdict: str = "?"          # NO TRADE | LONG | SHORT | ?
    entry: str = "-"
    stop_loss: str = "-"
    take_profit: str = "-"
    rr: str = "-"
    confidence: str = "-"       # low | medium | high | -
    raw: str = ""
    error: str | None = None
    parsed: bool = False


def load_system_prompt(path: str | Path) -> str:
    """Read the editable system prompt from disk at runtime."""
    return Path(path).read_text()


def build_user_message(payload: dict[str, Any]) -> str:
    """Serialize the payload compactly (no whitespace) to save tokens."""
    return json.dumps(payload, separators=(",", ":"))


# --- Response parsing ----------------------------------------------------

_OPTION_TO_VERDICT = {"A": "NO TRADE", "B": "LONG", "C": "SHORT"}
_NUM = r"[-+]?\d[\d,]*\.?\d*"


def _extract_section(text: str, header_regex: str) -> str:
    """Return text from a '### <header>' marker up to the next '###' or end."""
    m = re.search(header_regex, text, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"\n#{2,4}\s", text[start:])
    return text[start : start + nxt.start()] if nxt else text[start:]


def _first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def parse_verdict(symbol: str, text: str) -> Verdict:
    """Best-effort extraction of the verdict + trade parameters.

    Falls back to parsed=False (raw kept) if the structure can't be read.
    """
    v = Verdict(symbol=symbol, raw=text)
    verdict_section = _extract_section(text, r"#{2,4}\s*Verdict")
    scope = verdict_section or text

    # Direction: explicit keyword, else Option letter mapping.
    if re.search(r"\bNO\s*TRADE\b", scope, re.IGNORECASE):
        v.verdict = "NO TRADE"
    elif re.search(r"\bSHORT\b", scope, re.IGNORECASE):
        v.verdict = "SHORT"
    elif re.search(r"\bLONG\b", scope, re.IGNORECASE):
        v.verdict = "LONG"
    else:
        letter = _first(r"Option\s+([ABC])", scope)
        if letter:
            v.verdict = _OPTION_TO_VERDICT.get(letter.upper(), "?")

    # Confidence.
    conf = _first(r"confidence[:\s]*\**\s*(low|medium|high)", scope) or _first(
        r"\b(low|medium|high)\b\s*confidence", scope
    )
    if conf:
        v.confidence = conf.lower()

    # Trade params: pull from the chosen option's block when directional.
    block = text
    if v.verdict == "LONG":
        block = _extract_section(text, r"#{2,4}\s*Option\s*B") or text
    elif v.verdict == "SHORT":
        block = _extract_section(text, r"#{2,4}\s*Option\s*C") or text

    if v.verdict in ("LONG", "SHORT"):
        v.entry = _first(rf"Entry[:\s]*\**\s*({_NUM})", block) or v.entry
        v.stop_loss = _first(rf"Stop\s*Loss[:\s]*\**\s*({_NUM})", block) or v.stop_loss
        v.take_profit = (
            _first(rf"Take\s*Profit[:\s]*\**\s*({_NUM})", block) or v.take_profit
        )
        rr_line = re.search(r"\bR/?R\b[^\n]*", block, re.IGNORECASE)
        if rr_line:
            line = rr_line.group(0)
            # Prefer the last explicit ratio ("1.68:1"); the model may show
            # its arithmetic before it. Fallback: last bare number.
            ratios = re.findall(rf"{_NUM}\s*:\s*{_NUM}", line)
            if ratios:
                v.rr = re.sub(r"\s", "", ratios[-1])
            else:
                nums = re.findall(_NUM, line)
                if nums:
                    v.rr = nums[-1]

    v.parsed = v.verdict in ("NO TRADE", "LONG", "SHORT")
    return v


# --- API calls -----------------------------------------------------------

def _request_kwargs(model: str, max_tokens: int, system: str,
                    payload: dict[str, Any], effort: str | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": build_user_message(payload)}],
    }
    if effort:
        kwargs["output_config"] = {"effort": effort}
    return kwargs


def analyze_sync(client, model: str, max_tokens: int, system: str,
                 symbol: str, payload: dict[str, Any],
                 effort: str | None = None) -> Verdict:
    """Single blocking analysis call."""
    try:
        resp = client.messages.create(
            **_request_kwargs(model, max_tokens, system, payload, effort)
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return parse_verdict(symbol, text)
    except Exception as exc:  # noqa: BLE001 - surface any API error per-ticker
        return Verdict(symbol=symbol, error=str(exc))


async def analyze_batch_async(
    async_client, model: str, max_tokens: int, system: str,
    items: list[tuple[str, dict[str, Any]]], concurrency: int,
    effort: str | None = None,
) -> list[Verdict]:
    """Analyze many instruments concurrently, capped by a semaphore."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(symbol: str, payload: dict[str, Any]) -> Verdict:
        async with sem:
            try:
                resp = await async_client.messages.create(
                    **_request_kwargs(model, max_tokens, system, payload, effort)
                )
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "") == "text"
                )
                return parse_verdict(symbol, text)
            except Exception as exc:  # noqa: BLE001
                return Verdict(symbol=symbol, error=str(exc))

    return await asyncio.gather(*(_one(s, p) for s, p in items))
