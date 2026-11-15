"""Bounded MCP/model message contracts shared by the local reader and evaluator."""

import json
import re


class InvalidReply(ValueError):
    """A bounded model response does not match the host tool-call protocol."""


def visible_text(text: str) -> str:
    """Do not persist reasoning blocks, including an unfinished block."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.split("<think>", 1)[0].strip()


def parse_calls(text: str, remaining: int, *, allowed_names: set[str] | None = None) -> list[dict]:
    if len(text.encode()) > 16 * 1024:
        raise InvalidReply("model_reply_too_large")
    parts = re.findall(r"<tool_call>(.*?)</tool_call>", text, re.S)
    if text.count("<tool_call>") != len(parts) or text.count("</tool_call>") != len(parts):
        raise InvalidReply("unfinished_tool_call")
    calls = []
    for part in parts:
        try:
            call = json.loads(part)
        except (json.JSONDecodeError, RecursionError) as err:
            raise InvalidReply("invalid_tool_json") from err
        calls.append(call)
    return validate_calls(calls, remaining, allowed_names=allowed_names)


def validate_calls(
    calls: object, remaining: int, *, allowed_names: set[str] | None = None
) -> list[dict]:
    """Shared XML/native shape, name, argument and budget gate before any MCP RPC."""
    if not isinstance(calls, list):
        raise InvalidReply("invalid_tool_shape")
    if len(calls) > remaining:
        raise InvalidReply("tool_budget")
    for call in calls:
        if (
            not isinstance(call, dict)
            or set(call) != {"name", "arguments"}
            or not isinstance(call["name"], str)
            or not 1 <= len(call["name"]) <= 128
            or not isinstance(call["arguments"], dict)
        ):
            raise InvalidReply("invalid_tool_shape")
        if allowed_names is not None and call["name"] not in allowed_names:
            raise InvalidReply("tool_not_exposed")
    try:
        if len(json.dumps(calls, ensure_ascii=False, allow_nan=False).encode()) > 16 * 1024:
            raise InvalidReply("model_reply_too_large")
    except (TypeError, ValueError, RecursionError) as err:
        if isinstance(err, InvalidReply):
            raise
        raise InvalidReply("invalid_tool_arguments") from err
    return calls


def parse_native_calls(calls: object, remaining: int, *, allowed_names: set[str]) -> list[dict]:
    """Validate Ollama's structured calls directly; never reinterpret text as XML."""
    if not isinstance(calls, list):
        raise InvalidReply("invalid_tool_shape")
    normalized = []
    for call in calls:
        if (
            not isinstance(call, dict)
            or not {"function"} <= set(call) <= {"id", "type", "function"}
            or (
                "id" in call
                and (not isinstance(call["id"], str) or not 1 <= len(call["id"].encode()) <= 128)
            )
            or ("type" in call and call["type"] != "function")
            or not isinstance(function := call["function"], dict)
            or not {"name", "arguments"} <= set(function) <= {"index", "name", "arguments"}
            or (
                "index" in function
                and (type(function["index"]) is not int or function["index"] < 0)
            )
        ):
            raise InvalidReply("invalid_tool_shape")
        normalized.append({"name": function["name"], "arguments": function["arguments"]})
    return validate_calls(normalized, remaining, allowed_names=allowed_names)


def tool_functions(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["inputSchema"],
            },
        }
        for tool in tools
    ]


def prompt_text(response: dict) -> str:
    """Accept only the native API's single text prompt, never an error or attachment."""
    try:
        if "error" in response:
            raise ValueError
        messages = response["result"]["messages"]
        if (
            not isinstance(messages, list)
            or len(messages) != 1
            or messages[0]["role"] != "assistant"
            or messages[0]["content"]["type"] != "text"
            or not isinstance(text := messages[0]["content"]["text"], str)
            or not 1 <= len(text.encode()) <= 16 * 1024
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as err:
        raise InvalidReply("unexpected_prompt_response") from err
    return text
