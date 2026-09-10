"""
src/api/chat.py

In-app chatbot: answers natural-language questions about the tracked
options-volatility data using Claude tool-calling. Kept separate from
routes.py -- LLM orchestration (system prompt, tool loop, error taxonomy)
is a different concern from routes.py's "thin HTTP wrapper over dashboard
logic" rule, even though every tool here is itself a thin adapter over an
existing routes.py function (zero duplicated business logic either way).

Personal use only, same as the rest of this API -- see routes.py's module
docstring for the broader scope note.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Literal

import anthropic
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.symbols import SYMBOL_REGISTRY

from . import routes

# main.py never loads .env itself (only src/auth.py's SchwabAuth.from_env()
# does, lazily) -- this module needs ANTHROPIC_API_KEY in os.environ before
# the client is constructed below, so it loads .env independently.
load_dotenv()

router = APIRouter(prefix="/api")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
MAX_ITERATIONS = 6
MAX_TOOL_RESULT_CHARS = 20_000

_SYMBOL_ENUM = sorted(SYMBOL_REGISTRY.keys())

# Constructed only if the key is present -- import must never raise just
# because the key hasn't been added to .env yet; the route handler below
# checks for None and fails closed with a clean 503 instead.
_api_key = os.environ.get("ANTHROPIC_API_KEY")
_client = anthropic.Anthropic(api_key=_api_key) if _api_key else None


# ---------------------------------------------------------------------------
# Tool registry -- thin adapters over routes.py's existing, already-tested
# functions. Each executor calls its route function directly, in-process,
# with every parameter passed explicitly (bypassing the Query(...) sentinel
# defaults, which only resolve through FastAPI's own dependency injection).
# ---------------------------------------------------------------------------


def _tool_list_symbols() -> list[dict]:
    return [s.model_dump() for s in routes.list_symbols()]


def _tool_scanner(target_dte: int = 30) -> dict:
    return routes.scanner(target_dte=target_dte)


def _tool_scanner_pcr(expiration: str | None = None) -> dict:
    return routes.scanner_pcr(expiration=expiration)


def _tool_overview(symbols: str = "SPX", dte_min: int = 0, dte_max: int = 730) -> dict:
    return routes.overview(symbols=symbols, dte_min=dte_min, dte_max=dte_max)


def _tool_metric_series(
    symbol: str, metric: str = "atm_iv", target_dte: int = 30, lookback_days: int = 365
) -> dict:
    return routes.metric_series(symbol=symbol, metric=metric, target_dte=target_dte, lookback_days=lookback_days)


def _tool_trade_ideas(limit: int = 8) -> dict:
    return routes.trade_ideas(limit=limit)


@dataclass(frozen=True)
class ToolSpec:
    schema: dict
    executor: Callable[..., object]


TOOLS: dict[str, ToolSpec] = {
    "list_symbols": ToolSpec(
        schema={
            "name": "list_symbols",
            "description": "List every symbol currently tracked by the dashboard. Call this if you're "
            "unsure whether a ticker the user mentioned is actually tracked.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        executor=_tool_list_symbols,
    ),
    "scanner": ToolSpec(
        schema={
            "name": "scanner",
            "description": (
                "Current (latest-snapshot) cross-symbol table: skew, ATM IV, 25-delta IV, curvature, IV "
                "richness z-score/label, IV rank/percentile, realized vol, underlying price -- ONE ROW PER "
                "TRACKED SYMBOL, in a single call. Use this for any 'which symbol has the highest/lowest "
                "current X' or 'which symbols are Y right now' question across the whole universe. Do not "
                "call this once per symbol -- it already returns every symbol."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_dte": {
                        "type": "integer",
                        "description": "Target days-to-expiration each symbol's row is drawn from "
                        "(closest available). Default 30.",
                        "default": 30,
                    },
                },
                "required": [],
            },
        },
        executor=_tool_scanner,
    ),
    "scanner_pcr": ToolSpec(
        schema={
            "name": "scanner_pcr",
            "description": (
                "Cross-symbol put/call open-interest ratio at one expiration -- a positioning/sentiment "
                "read, not a smile-shape read. Use for 'which symbol has the most put-heavy (or call-heavy) "
                "open interest' style questions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "expiration": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD). Omit to use the expiration nearest 30 DTE.",
                    },
                },
                "required": [],
            },
        },
        executor=_tool_scanner_pcr,
    ),
    "overview": ToolSpec(
        schema={
            "name": "overview",
            "description": (
                "Full per-expiry term-structure / skew / curvature / VRP CURVES (one data point per DTE) "
                "for one or more symbols. This is the ONLY tool with per-DTE points -- use it for term "
                "structure SHAPE, SLOPE, or DIFFERENTIAL questions ('term structure differential', "
                "'steepest curve', 'inverted term structure') and for comparing curve shape across symbols. "
                "Pass multiple symbols comma-separated (e.g. 'SPX,AAPL,NVDA') to compare several in ONE "
                "call rather than calling this once per symbol."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "string",
                        "description": f"Comma-separated tracked symbols, e.g. 'SPX,AAPL'. Valid symbols: "
                        f"{', '.join(_SYMBOL_ENUM)}.",
                        "default": "SPX",
                    },
                    "dte_min": {"type": "integer", "description": "Lower DTE bound. Default 0.", "default": 0},
                    "dte_max": {
                        "type": "integer",
                        "description": "Upper DTE bound. Default 730. Narrow this (e.g. 7-90) for "
                        "near-dated-only questions.",
                        "default": 730,
                    },
                },
                "required": [],
            },
        },
        executor=_tool_overview,
    ),
    "metric_series": ToolSpec(
        schema={
            "name": "metric_series",
            "description": (
                "Day-over-day historical time series of ONE metric for ONE symbol -- use for 'has X been "
                "consistently Y over time' or trend questions. Per-symbol only: for a 'which symbol is most "
                "consistent' question, first narrow candidates with scanner/overview, THEN call this once "
                "per narrowed candidate -- do not loop this over the whole tracked universe."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "enum": _SYMBOL_ENUM, "description": "Tracked symbol."},
                    "metric": {
                        "type": "string",
                        "enum": ["atm_iv", "skew", "curvature", "realized_vol", "vrp"],
                        "default": "atm_iv",
                    },
                    "target_dte": {"type": "integer", "default": 30},
                    "lookback_days": {
                        "type": "integer",
                        "description": "How many trailing calendar days of history to include. Default 365.",
                        "default": 365,
                    },
                },
                "required": ["symbol"],
            },
        },
        executor=_tool_metric_series,
    ),
    "trade_ideas": ToolSpec(
        schema={
            "name": "trade_ideas",
            "description": "Current top actionable trade candidates ranked by conviction. Use for 'what "
            "looks interesting to trade right now' style questions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max ideas to return. Default 8.", "default": 8},
                },
                "required": [],
            },
        },
        executor=_tool_trade_ideas,
    ),
}


SYSTEM_PROMPT = f"""You are the in-app assistant for Vol Dashboard, a personal options-volatility research \
tool. You answer questions about the user's own tracked symbols using the tools below -- never guess or \
hallucinate a number. If a tool doesn't return what you need, say so plainly rather than making something up.

Tracked symbols: {', '.join(_SYMBOL_ENUM)}. If you're unsure whether a ticker is tracked, call list_symbols.

Tool selection:
- scanner: current cross-symbol snapshot (skew, IV, curvature, richness, IV rank) -- one call covers every \
symbol. Start here for "which symbol has the highest/lowest current X" questions.
- overview: per-DTE term-structure/skew/curvature/VRP CURVES. The only tool with per-expiry shape data -- \
use it for term-structure slope/differential and multi-symbol curve comparisons. Pass every symbol you \
need in one comma-separated call.
- scanner_pcr: cross-symbol put/call open-interest ratio (positioning, not smile shape).
- metric_series: historical day-over-day series for ONE symbol+metric -- use for "has X been consistently \
Y" questions. Narrow your candidates with scanner/overview FIRST, then call this per candidate. Never loop \
it over the whole tracked universe.
- trade_ideas: current actionable trade candidates.

Efficiency: for any question spanning multiple or all symbols, call scanner or overview ONCE with every \
relevant symbol rather than calling a per-symbol tool many times. Reserve per-symbol tools for a shortlist \
you've already narrowed down.

Answer style: be concise -- this is a chat panel, not a report. Cite the actual numbers you used. If a \
field came back null, say the data isn't available rather than omitting it silently or inventing a value.

Not investment advice: describe what the data shows (skew direction, IV level, historical pattern, which \
expiry looks notable) -- do not tell the user what to buy or sell, predict returns, or frame anything as a \
recommendation. If a question implies "should I trade this", answer with what the data shows and note that \
this is informational only, not investment advice."""


# ---------------------------------------------------------------------------
# Tool-calling loop
# ---------------------------------------------------------------------------


def _bounded_json(result: object) -> str:
    text = json.dumps(result, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + (
            f"... [truncated, {len(text)} chars total -- narrow the symbols/dte range and ask again for "
            "more detail]"
        )
    return text


def _execute_tool(block) -> dict:
    spec = TOOLS.get(block.name)
    if spec is None:
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Unknown tool '{block.name}'.",
            "is_error": True,
        }
    try:
        result = spec.executor(**block.input)
    except HTTPException as exc:
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Error {exc.status_code}: {exc.detail}",
            "is_error": True,
        }
    except Exception as exc:  # never let one bad tool call crash the endpoint
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Tool '{block.name}' failed: {exc}",
            "is_error": True,
        }
    return {"type": "tool_result", "tool_use_id": block.id, "content": _bounded_json(result)}


def _extract_text(response) -> str:
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or "I wasn't able to put together an answer for that."


def run_chat_turn(message: str, history: list[dict]) -> str:
    messages: list[dict] = [*history, {"role": "user", "content": message}]
    tool_schemas = [spec.schema for spec in TOOLS.values()]

    for _ in range(MAX_ITERATIONS):
        response = _client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tool_schemas,
            messages=messages,
            output_config={"effort": "medium"},
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return _extract_text(response)

        tool_results = [_execute_tool(b) for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": tool_results})

    # Iteration cap hit -- force one final prose answer from whatever was
    # already gathered instead of looping forever or returning nothing.
    final = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=tool_schemas,
        tool_choice={"type": "none"},
        messages=messages,
        output_config={"effort": "medium"},
    )
    return _extract_text(final)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat")
def chat(req: ChatRequest) -> ChatResponse:
    if _client is None:
        raise HTTPException(503, "Chat is not configured -- set ANTHROPIC_API_KEY in .env and restart the server.")
    try:
        reply = run_chat_turn(req.message, [m.model_dump() for m in req.history])
    except anthropic.AuthenticationError:
        raise HTTPException(503, "Anthropic API key was rejected -- check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Rate limited by the Anthropic API -- try again shortly.")
    except anthropic.APIConnectionError:
        raise HTTPException(503, "Could not reach the Anthropic API.")
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Anthropic API error: {exc.message}")
    return ChatResponse(reply=reply)
