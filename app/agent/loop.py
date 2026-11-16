"""The agent loop: streams Claude's reply to the browser and runs tools between turns.

History is append-only (each assistant message is stored exactly as returned, thinking blocks included),
which keeps prompt caching effective and replays thinking correctly.
"""

from __future__ import annotations

import datetime as dt
from typing import AsyncIterator

import anthropic

from ..context import AppContext
from ..usage import budget_error, record
from .prompt import SYSTEM_PROMPT
from .tools import execute, tool_definitions

MAX_STEPS = 30
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _system() -> list[dict]:
    today = dt.date.today().isoformat()
    return [{"type": "text", "text": SYSTEM_PROMPT + f"\nToday's date is {today}."}]


def _error_results(tool_uses, message: str) -> list[dict]:
    return [{"type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": message} for tu in tool_uses]


async def run_turn(ctx: AppContext, conversation_id: int, user_text: str) -> AsyncIterator[dict]:
    if ctx.anthropic is None:
        yield {"type": "error", "message": "ANTHROPIC_API_KEY is not set on the server."}
        return
    store = ctx.store
    history = [{"role": m["role"], "content": m["content"]} for m in store.messages(conversation_id)]
    # A previous turn interrupted mid-tool (e.g. the browser closed) leaves tool calls without results.
    if history and history[-1]["role"] == "assistant" and isinstance(history[-1]["content"], list):
        dangling = [b["id"] for b in history[-1]["content"] if b.get("type") == "tool_use"]
        if dangling:
            results = [{"type": "tool_result", "tool_use_id": i, "is_error": True, "content": "Interrupted before it ran."} for i in dangling]
            history.append({"role": "user", "content": results})
            store.append_message(conversation_id, "user", results)
    user_msg = {"role": "user", "content": user_text}
    history.append(user_msg)
    store.append_message(conversation_id, "user", user_text)
    tools = tool_definitions()
    json_retries = 0

    for _step in range(MAX_STEPS):
        over = budget_error(store)
        if over:
            yield {"type": "error", "message": over}
            return
        try:
            async with ctx.anthropic.beta.messages.stream(
                model=ctx.settings.model,
                max_tokens=64000,
                system=_system(),
                tools=tools,
                messages=history,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": ctx.settings.effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_start" and event.content_block.type == "tool_use":
                        yield {"type": "tool_pending", "name": event.content_block.name}
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield {"type": "text", "text": event.delta.text}
                        elif event.delta.type == "thinking_delta" and event.delta.thinking:
                            yield {"type": "thinking", "text": event.delta.thinking}
                message = await stream.get_final_message()
            json_retries = 0
        except ValueError:
            # Tool input JSON the SDK couldn't parse at all; the block never completed, so re-issue the turn.
            json_retries += 1
            if json_retries > 2:
                yield {"type": "error", "message": "The model produced malformed tool input repeatedly."}
                return
            continue
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "Claude API rate limit reached. Wait a minute and try again."}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "message": f"Claude API error {e.status_code}: {e.message}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Could not reach the Claude API."}
            return

        if message.usage:
            record(store, "chat", message.model or ctx.settings.model, message.usage, conversation_id)
        content = [b.to_dict(mode="json") for b in message.content]
        history.append({"role": "assistant", "content": content})
        store.append_message(conversation_id, "assistant", content)

        if message.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in message.content if b.type == "tool_use"]

        if message.stop_reason == "refusal":
            if tool_uses:
                results = _error_results(tool_uses, "Not run: the response was declined.")
                history.append({"role": "user", "content": results})
                store.append_message(conversation_id, "user", results)
            yield {"type": "error", "message": "Claude declined this request."}
            return
        if not tool_uses:
            yield {"type": "done", "usage": message.usage.to_dict(mode="json") if message.usage else None}
            return
        if message.stop_reason == "max_tokens":
            results = _error_results(tool_uses, "Not run: the tool input was cut off by the output limit. Retry with a smaller input.")
            history.append({"role": "user", "content": results})
            store.append_message(conversation_id, "user", results)
            continue

        results = []
        for tu in tool_uses:
            yield {"type": "tool_start", "id": tu.id, "name": tu.name, "input": tu.input}
            outcome = await execute(ctx, conversation_id, tu.name, tu.input)
            for ev in outcome.events:
                yield ev
            yield {"type": "tool_end", "id": tu.id, "name": tu.name, "ok": not outcome.is_error,
                   "preview": outcome.content[:400]}
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": outcome.content,
                            **({"is_error": True} if outcome.is_error else {})})
        history.append({"role": "user", "content": results})
        store.append_message(conversation_id, "user", results)

    yield {"type": "error", "message": f"Stopped after {MAX_STEPS} steps. Ask me to continue if needed."}
