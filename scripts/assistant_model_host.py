"""Optional CI-only reusable completion process; no product or provider integration."""

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scripts.assistant_model import CONTEXT_TOKENS, MAX_TOKENS, expired_result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ReusableCompletion:
    """One owned loopback process, bounded requests and no inherited tool configuration."""

    def __init__(self, binary: Path, weights: Path) -> None:
        self.binary, self.weights = binary, weights
        self.process: subprocess.Popen | None = None
        self.closed = False
        self._token = secrets.token_urlsafe(32)
        self._cache = False
        self._url = ""
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def reset(self) -> None:
        """Independent cases/arms must not reuse a preceding case's prompt cache."""
        self._cache = False

    def _kill(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def close(self) -> None:
        self.closed = True
        process, self.process = self.process, None
        self._cache = False
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            process.wait(timeout=2)

    def _request(self, path: str, deadline: float, data: dict | None = None) -> dict:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError
        request = Request(
            self._url + path,
            data=json.dumps(data, allow_nan=False).encode() if data is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        with self._opener.open(request, timeout=remaining) as response:
            body = response.read(64 * 1024 + 1)
        if len(body) > 64 * 1024:
            raise ValueError("Evaluation response exceeds its bound")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Invalid evaluation response")
        return result

    def _start(self, deadline: float) -> None:
        if self.closed:
            raise RuntimeError("Evaluation host is closed")
        if self.process is not None:
            if self.process.poll() is not None:
                raise RuntimeError("Evaluation process exited")
            return
        # A competing bind fails the owned process; requests still require its
        # unpredictable per-owner credential. No configured/external host exists.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self._url = f"http://127.0.0.1:{port}"
        self.process = subprocess.Popen(
            [
                str(self.binary),
                "-m",
                str(self.weights),
                "-c",
                str(CONTEXT_TOKENS),
                "-t",
                "2",
                "-ngl",
                "0",
                "--parallel",
                "1",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-agent",
                "--no-webui",
                "--no-webui-mcp-proxy",
                "--no-warmup",
                "--log-disable",
            ],
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "LD_LIBRARY_PATH": str(self.binary.parent),
                "LLAMA_API_KEY": self._token,
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        process = self.process
        if self.closed:
            self.close()
            raise RuntimeError("Evaluation host closed during startup")
        while time.perf_counter() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Evaluation process exited during startup")
            try:
                if (
                    self._request("/health", min(deadline, time.perf_counter() + 0.5)).get("status")
                    == "ok"
                ):
                    return
            except HTTPError as err:
                if err.code != 503:
                    raise
            except (URLError, TimeoutError):
                pass
            time.sleep(min(0.05, max(0, deadline - time.perf_counter())))
        raise TimeoutError

    def complete(self, prompt: str, deadline: float) -> dict:
        started = time.perf_counter()
        if started >= deadline:
            return expired_result(started)
        watchdog = threading.Timer(max(0, deadline - started), self._kill)
        watchdog.start()
        try:
            self._start(deadline)
            result = self._request(
                "/completion",
                deadline,
                {
                    "prompt": prompt,
                    "n_predict": MAX_TOKENS,
                    "temperature": 0,
                    "seed": 0,
                    "stream": False,
                    "id_slot": 0,
                    "cache_prompt": self._cache,
                },
            )
            text = result.get("content")
            if (
                not isinstance(text, str)
                or len(text.encode()) > 16 * 1024
                or result.get("stop_type") not in {"eos", "limit", "word"}
                or type(result.get("truncated")) is not bool
            ):
                raise ValueError("Invalid completion shape")
            counts = {}
            for source, target, bound in (
                ("tokens_evaluated", "engine_prompt_tokens", CONTEXT_TOKENS),
                ("tokens_predicted", "engine_decoded_tokens", MAX_TOKENS),
                ("tokens_cached", "engine_cached_tokens", CONTEXT_TOKENS),
            ):
                value = result.get(source)
                if type(value) is not int or not 0 <= value <= bound:
                    raise ValueError("Invalid completion counters")
                counts[target] = value
            if time.perf_counter() >= deadline:
                raise TimeoutError
            self._cache = True
            return {
                "text": text,
                "seconds": time.perf_counter() - started,
                "deadline_limited": False,
                "length_limited": result["stop_type"] == "limit",
                "context_limited": result["truncated"],
                **counts,
            }
        except (TimeoutError, URLError, HTTPException, OSError, ValueError, RuntimeError):
            self.close()
            if time.perf_counter() >= deadline:
                return expired_result(started)
            raise RuntimeError("Evaluation completion process failed") from None
        finally:
            watchdog.cancel()
            watchdog.join(timeout=2)
