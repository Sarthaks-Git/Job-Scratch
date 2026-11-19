"""Thin wrapper around the Anthropic SDK used by every part of the agent.

- Streaming turns with client tools (the chat agent and the apply agent).
- Structured JSON outputs validated with pydantic (resume parsing, job scoring).
- Server-side refusal fallbacks are on: if Claude declines a request, the API
  re-runs it on a fallback model inside the same call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class Tool:
    """A client-side tool: name, description, pydantic input model, handler."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]

    def definition(self) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
            # Tool inputs stream as generated; we validate them with pydantic below.
            "eager_input_streaming": True,
        }


@dataclass
class TurnResult:
    message: Any  # BetaMessage
    text: str
    tool_uses: list[Any] = field(default_factory=list)


class LLM:
    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic(max_retries=4)

    # ---------------------------------------------------------- streaming ---
    def stream_turn(
        self,
        *,
        system: list[dict] | str,
        messages: list[dict],
        tools: list[dict],
        effort: str,
        on_text: Callable[[str], None] | None = None,
        max_tokens: int = 32000,
    ) -> TurnResult:
        """One model call. Streams text to on_text and returns the final message."""
        attempts = 0
        while True:
            attempts += 1
            try:
                with self.client.beta.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools,
                    output_config={"effort": effort},
                    cache_control={"type": "ephemeral"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                ) as stream:
                    for event in stream:
                        if event.type == "text" and on_text:
                            on_text(event.text)
                    message = stream.get_final_message()
            except ValueError:
                # Malformed streamed tool-input JSON: the SDK raises before the block completes,
                # so there is no tool_use_id to answer. Re-issue the request (bounded).
                if attempts >= 3:
                    raise LLMError("Claude produced unparseable tool input three times in a row.")
                continue
            if message.stop_reason == "max_tokens" and any(
                b.type == "tool_use" for b in message.content
            ):
                if attempts >= 2:
                    raise LLMError("Response was cut off at max_tokens in the middle of a tool call.")
                max_tokens = min(max_tokens * 2, 128000)
                continue
            text = "".join(b.text for b in message.content if b.type == "text")
            tool_uses = [b for b in message.content if b.type == "tool_use"]
            return TurnResult(message=message, text=text, tool_uses=tool_uses)

    # --------------------------------------------------------- structured ---
    def structured(
        self,
        *,
        schema: type[T],
        system: str,
        content: list[dict] | str,
        effort: str = "low",
        max_tokens: int = 16000,
    ) -> T:
        """Ask for JSON matching `schema` and return the validated object."""
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("Structured response was cut off (max_tokens).")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            raise LLMError(f"Claude's JSON did not match the expected schema: {e}") from e


def run_tool(tool: Tool, raw_input: Any) -> tuple[Any, bool]:
    """Validate input with pydantic, run the handler. Returns (content, is_error)."""
    try:
        args = tool.input_model.model_validate(raw_input)
    except ValidationError as e:
        return json.dumps({"INVALID_INPUT": json.dumps(raw_input, default=str), "errors": e.errors(include_url=False)}, default=str), True
    try:
        result = tool.handler(args)
    except Exception as e:  # tool failures go back to Claude so it can adapt
        return f"Error running {tool.name}: {type(e).__name__}: {e}", True
    if isinstance(result, (list, dict)) and not _is_content_blocks(result):
        return json.dumps(result, ensure_ascii=False, default=str), False
    return result, False


def _is_content_blocks(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(v, dict) and v.get("type") in {"text", "image"} for v in value
    )


def friendly_api_error(e: Exception) -> str:
    """Map SDK exceptions to a message the CLI can show."""
    if isinstance(e, anthropic.AuthenticationError):
        return "Authentication failed: set ANTHROPIC_API_KEY to a valid Claude API key."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "Your API key is not allowed to use this model or feature."
    if isinstance(e, anthropic.NotFoundError):
        return "Model not found. Check the 'model' setting (/settings)."
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the Claude API. Wait a minute and try again."
    if isinstance(e, anthropic.BadRequestError):
        return f"The Claude API rejected the request: {e.message}"
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude API error {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Could not reach the Claude API. Check your internet connection."
    return f"{type(e).__name__}: {e}"
