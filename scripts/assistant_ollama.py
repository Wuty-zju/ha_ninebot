"""Bounded local Ollama transport for the existing synthetic evaluator only."""

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scripts.assistant_model import CONTEXT_TOKENS, MAX_TOKENS, expired_result

MODEL = "qwen3.5:4b-q4_K_M"
DIGEST = "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04"
DEFAULT_URL = "http://127.0.0.1:11434"
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
API_PATHS = {"/api/version", "/api/tags", "/api/show", "/api/chat", "/api/generate"}


class OllamaError(RuntimeError):
    """Fixed error kind; never include a peer body or transport exception."""


def local_url(url: str) -> str:
    """Allow only explicit local HTTP authorities, without URL decoration."""
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "localhost", "::1", "host.lima.internal"}
            or parts.username is not None
            or parts.password is not None
            or parts.path
            or parts.query
            or parts.fragment
            or any(char.isspace() or ord(char) < 32 for char in url)
            or "?" in url
            or "#" in url
        ):
            raise ValueError
        port = parts.port if parts.port is not None else 11434
        if not 1 <= port <= 65535:
            raise ValueError
        host = f"[{parts.hostname}]" if parts.hostname == "::1" else parts.hostname
        if parts.netloc not in {host, f"{host}:{port}"}:
            raise ValueError
    except (TypeError, ValueError):
        raise OllamaError("invalid_local_endpoint") from None
    return f"http://{host}:{port}"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OllamaError("redirect_refused")


def http_worker() -> None:
    """One HTTP transaction; parent kills/reaps us even during a slow drip body."""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise OllamaError("request_too_large")
        request = json.loads(raw)
        endpoint = local_url(request["url"])
        path = request["path"]
        if path not in API_PATHS:
            raise OllamaError("invalid_api_path")
        body = request["body"]
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        opener = build_opener(ProxyHandler({}), NoRedirect())
        http_request = Request(
            endpoint + path, data=data, headers={"Content-Type": "application/json"}
        )
        with opener.open(http_request, timeout=request["timeout"]) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise OllamaError("response_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict) or "error" in value:
            raise OllamaError("invalid_api_response")
        result = {"response": value}
    except OllamaError as err:
        result = {"error": str(err)}
    except HTTPError:
        result = {"error": "http_error"}
    except (URLError, OSError, TimeoutError):
        result = {"error": "transport_error"}
    except (KeyError, TypeError, ValueError, RecursionError):
        result = {"error": "invalid_api_response"}
    sys.stdout.write(json.dumps(result, allow_nan=False))


def request_json(url: str, path: str, body: dict | None, deadline: float) -> dict:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise OllamaError("deadline")
    packet = json.dumps(
        {"url": local_url(url), "path": path, "body": body, "timeout": remaining},
        allow_nan=False,
    ).encode()
    if len(packet) > MAX_REQUEST_BYTES:
        raise OllamaError("request_too_large")
    try:
        # -m keeps the same import context as the evaluator; no shell or persistent worker.
        result = subprocess.run(
            [sys.executable, "-m", "scripts.assistant_ollama", "--http-worker"],
            cwd=Path(__file__).resolve().parents[1],
            input=packet,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(0.001, deadline - time.perf_counter()),
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise OllamaError("deadline") from None
    except (OSError, subprocess.CalledProcessError):
        raise OllamaError("worker_failed") from None
    try:
        result = json.loads(result.stdout)
        if "error" in result:
            raise OllamaError(result["error"])
        return result["response"]
    except (KeyError, TypeError, ValueError, RecursionError):
        raise OllamaError("invalid_worker_response") from None


def ollama_messages(messages: list[dict]) -> list[dict]:
    """Translate host IDs to Ollama's native tool_name; keep argument objects."""
    native = []
    for message in messages:
        item = {"role": message["role"], "content": message["content"]}
        if item["role"] == "tool":
            item["tool_name"] = message["name"]
        if "tool_calls" in message:
            item["tool_calls"] = [
                {"function": {"index": index, **call["function"]}}
                for index, call in enumerate(message["tool_calls"])
            ]
        native.append(item)
    return native


class OllamaModel:
    """Fixed installed model, native tools and no model download or service ownership."""

    def __init__(self, url: str = DEFAULT_URL) -> None:
        self.url = local_url(url)
        self._closed = False
        deadline = time.perf_counter() + 15
        version = request_json(self.url, "/api/version", None, deadline).get("version")
        tags = request_json(self.url, "/api/tags", None, deadline).get("models")
        if not isinstance(version, str) or not 1 <= len(version) <= 128:
            raise OllamaError("invalid_version")
        if not isinstance(tags, list):
            raise OllamaError("invalid_model_inventory")
        installed = [item for item in tags if isinstance(item, dict) and item.get("name") == MODEL]
        if len(installed) != 1:
            raise OllamaError("model_not_installed")
        if installed[0].get("digest") != DIGEST:
            raise OllamaError("model_digest_mismatch")
        shown = request_json(self.url, "/api/show", {"model": MODEL}, deadline)
        info = shown.get("model_info", {})
        self.metadata = {
            "engine": "ollama",
            "ollama_version": version,
            "model": MODEL,
            "model_digest": installed[0]["digest"],
            "completion_host": "ollama",
            "endpoint": self.url,
            "context_tokens": CONTEXT_TOKENS,
            "temperature": 0,
            "seed": 42,
            "thinking_control": "think_false",
            "stream": False,
            "token_count_basis": "native prompt_eval_count/eval_count, including server accounting",
            "host_lifecycle": "external_shared_service",
            "cache_reset": "fixed_model_unload_keep_alive_zero",
            "first_reply_includes_startup": True,
            "reset_count": 0,
            "client_hardware": {
                "system": platform.system(),
                "machine": platform.machine(),
                "reported_cpus": os.cpu_count(),
            },
            "server_hardware": None,
            "model_context_lengths": {
                key: value
                for key, value in info.items()
                if key.endswith(".context_length") and type(value) is int
            }
            if isinstance(info, dict)
            else {},
        }

    def reset(self) -> None:
        if self._closed:
            raise OllamaError("host_closed")
        result = request_json(
            self.url,
            "/api/generate",
            {"model": MODEL, "keep_alive": 0, "stream": False},
            time.perf_counter() + 15,
        )
        if result.get("done") is not True or result.get("done_reason") != "unload":
            raise OllamaError("model_unload_unconfirmed")
        self.metadata["reset_count"] += 1

    def close(self) -> None:
        # No owned server, session, worker, or background thread survives a request.
        self._closed = True

    def reply(self, messages: list[dict], tools: list[dict], deadline: float) -> dict:
        if self._closed:
            raise OllamaError("host_closed")
        started = time.perf_counter()
        try:
            response = request_json(
                self.url,
                "/api/chat",
                {
                    "model": MODEL,
                    "messages": ollama_messages(messages),
                    "tools": tools,
                    "think": False,
                    "stream": False,
                    "options": {
                        "num_ctx": CONTEXT_TOKENS,
                        "num_predict": MAX_TOKENS,
                        "temperature": 0,
                        "seed": 42,
                    },
                },
                deadline,
            )
            message = response.get("message")
            if (
                response.get("done") is not True
                or response.get("model") != MODEL
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str)
                or len(message["content"].encode()) > 16 * 1024
            ):
                raise OllamaError("invalid_chat_response")
            text = message["content"]
            if "</think>" in text:
                text = text.rsplit("</think>", 1)[1]
            text = text.split("<think>", 1)[0].strip()
            calls = message.get("tool_calls", [])
            if not text and not calls:
                raise OllamaError("empty_chat_response")
            stats = {}
            for key in (
                "prompt_eval_count",
                "eval_count",
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
            ):
                value = response.get(key)
                if value is not None and (type(value) is not int or value < 0):
                    raise OllamaError("invalid_chat_statistics")
                stats[key] = value
            return {
                "text": text,
                "tool_calls": calls,
                "input_tokens": stats["prompt_eval_count"],
                "output_tokens": stats["eval_count"],
                "seconds": time.perf_counter() - started,
                "length_limited": response.get("done_reason") == "length",
                "deadline_limited": False,
                "native_statistics": stats,
            }
        except OllamaError as err:
            result = expired_result(started)
            result["deadline_limited"] = str(err) == "deadline"
            result["failure_kind"] = str(err)
            return result


if __name__ == "__main__" and sys.argv[1:] == ["--http-worker"]:
    http_worker()
