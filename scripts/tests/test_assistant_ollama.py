"""Real bounded local peers; never a real model, external host or HA instance."""

import json
import os
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from scripts.assistant_ollama import (
    DIGEST,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MODEL,
    OllamaError,
    OllamaModel,
    local_url,
    ollama_messages,
    request_json,
)


def chat_response(**changes):
    return {
        "model": MODEL,
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": "Synthetic answer"},
        "prompt_eval_count": 32,
        "eval_count": 8,
        "total_duration": 1000,
        "load_duration": 100,
        "prompt_eval_duration": 400,
        "eval_duration": 500,
        **changes,
    }


@contextmanager
def peer(respond=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def log_message(self, *args):
            pass

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(body) if body else None
            requests.append((self.path, body))
            value = {
                "/api/version": {"version": "synthetic-1"},
                "/api/tags": {"models": [{"name": MODEL, "digest": DIGEST}]},
                "/api/show": {"model_info": {"qwen35.context_length": 262144}},
                "/api/generate": {"done": True, "done_reason": "unload"},
                "/api/chat": chat_response(),
            }[self.path]
            if respond is not None:
                value = respond(self, self.path, body, value)
            if value is None:
                return
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


class OllamaTests(unittest.TestCase):
    def test_only_explicit_local_http_authorities_are_accepted(self):
        for host in ("127.0.0.1", "localhost", "[::1]", "host.lima.internal"):
            self.assertEqual(local_url(f"http://{host}"), f"http://{host}:11434")
            self.assertEqual(local_url(f"http://{host}:1234"), f"http://{host}:1234")
        for url in (
            "https://127.0.0.1",
            "http://127.1",
            "http://2130706433",
            "http://127.0.0.2",
            "http://example.com",
            "http://localhost.evil",
            "http://localhost/",
            "http://localhost/api/chat",
            "http://localhost?secret=value",
            "http://localhost?",
            "http://localhost#",
            "http://user:secret@localhost",
            "http://localhost:0",
            "http://localhost:65536",
            "http://localhost:",
            "http://localhost\n",
            "http://LOCALHOST",
        ):
            with (
                self.subTest(url=url),
                self.assertRaisesRegex(OllamaError, "invalid_local_endpoint"),
            ):
                local_url(url)

    def test_no_proxy_environment_is_used_and_redirect_is_not_followed(self):
        with (
            peer() as (url, requests),
            patch.dict(
                os.environ,
                {
                    "http_proxy": "http://127.0.0.1:1",
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "ALL_PROXY": "http://127.0.0.1:1",
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            ),
        ):
            self.assertEqual(
                request_json(url, "/api/version", None, time.perf_counter() + 2),
                {"version": "synthetic-1"},
            )
            self.assertEqual(len(requests), 1)
        with peer() as (destination, destination_requests):

            def redirect(handler, path, body, value):
                handler.send_response(302)
                handler.send_header("Location", destination + "/api/version?private=do-not-follow")
                handler.end_headers()
                return None

            with peer(redirect) as (url, requests):
                with self.assertRaisesRegex(OllamaError, "redirect_refused"):
                    request_json(url, "/api/version", None, time.perf_counter() + 2)
                self.assertEqual(len(requests), 1)
                self.assertEqual(destination_requests, [])

    def test_inventory_digest_must_match_before_show_or_chat(self):
        for models, kind in (
            ([], "model_not_installed"),
            ([{"name": MODEL}], "model_digest_mismatch"),
            ([{"name": MODEL, "digest": "unexpected"}], "model_digest_mismatch"),
        ):

            def inventory(handler, path, body, value, models=models):
                return {"models": models} if path == "/api/tags" else value

            with self.subTest(kind=kind), peer(inventory) as (url, requests):
                with self.assertRaisesRegex(OllamaError, kind):
                    OllamaModel(url)
                self.assertEqual([path for path, _ in requests], ["/api/version", "/api/tags"])

    def test_native_tools_options_statistics_and_reasoning_redaction(self):
        calls = [{"function": {"index": 0, "name": "tool", "arguments": {"refresh": False}}}]

        def response(handler, path, body, value):
            if path == "/api/chat":
                return chat_response(
                    message={
                        "role": "assistant",
                        "content": "<think>private-content</think>Visible",
                        "thinking": "private-thinking",
                        "tool_calls": calls,
                    }
                )
            return value

        with peer(response) as (url, requests):
            model = OllamaModel(url)
            model.reset()
            tools = [{"type": "function", "function": {"name": "tool", "parameters": {}}}]
            result = model.reply(
                [{"role": "user", "content": "Synthetic"}], tools, time.perf_counter() + 3
            )
            self.assertEqual(result["text"], "Visible")
            self.assertEqual(result["tool_calls"], calls)
            self.assertEqual(result["input_tokens"], 32)
            self.assertEqual(result["output_tokens"], 8)
            self.assertEqual(result["native_statistics"]["total_duration"], 1000)
            self.assertNotIn("private", json.dumps(result))
            self.assertNotIn("thinking", json.dumps(result))
            self.assertEqual(model.metadata["model_digest"], DIGEST)
            self.assertEqual(model.metadata["ollama_version"], "synthetic-1")
            self.assertEqual(model.metadata["reset_count"], 1)
            self.assertIsNone(model.metadata["server_hardware"])
            chat = requests[-1][1]
            self.assertFalse(chat["think"])
            self.assertFalse(chat["stream"])
            self.assertEqual(chat["tools"], tools)
            self.assertEqual(
                chat["options"],
                {"num_ctx": 8192, "num_predict": 256, "temperature": 0, "seed": 42},
            )
            self.assertEqual(
                requests[-2],
                ("/api/generate", {"model": MODEL, "keep_alive": 0, "stream": False}),
            )
            model.close()
            model.close()
            with self.assertRaisesRegex(OllamaError, "host_closed"):
                model.reply([], [], time.perf_counter() + 3)
            self.assertFalse(any("pull" in path for path, _ in requests))

    def test_host_tool_messages_keep_dict_arguments_and_use_native_tool_name(self):
        arguments = {"vehicle_ref": "fixture", "include_daily": False}
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "tool", "arguments": arguments},
                    }
                ],
            },
            {"role": "tool", "name": "tool", "tool_call_id": "call_1", "content": "{}"},
        ]
        native = ollama_messages(messages)
        self.assertEqual(native[1], {"role": "tool", "tool_name": "tool", "content": "{}"})
        function = native[0]["tool_calls"][0]["function"]
        self.assertIs(function["arguments"], arguments)
        self.assertEqual(function["index"], 0)
        self.assertEqual(native[0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(native[0]["tool_calls"][0]["type"], "function")
        self.assertNotIn("tool_call_id", json.dumps(native))

    def test_recorded_native_call_id_survives_assistant_replay_without_receipt_id(self):
        call = {
            "id": "call_pt5p187g",
            "function": {"index": 0, "name": "synthetic_add", "arguments": {"a": 17, "b": 25}},
        }
        for extra in ({}, {"type": "function"}):
            messages = [
                {"role": "assistant", "content": "", "tool_calls": [call | extra]},
                {
                    "role": "tool",
                    "name": "synthetic_add",
                    "tool_call_id": call["id"],
                    "content": "42",
                },
            ]
            replay = ollama_messages(messages)
            self.assertEqual(replay[0]["tool_calls"], [call | extra])
            self.assertEqual(
                replay[1], {"role": "tool", "tool_name": "synthetic_add", "content": "42"}
            )
            self.assertNotIn("tool_call_id", replay[1])

    def test_true_slow_drip_deadline_and_next_case_recovery(self):
        first = True

        def drip(handler, path, body, value):
            nonlocal first
            if path != "/api/chat" or not first:
                return value
            first = False
            data = json.dumps(value).encode()
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            for byte in data:
                try:
                    handler.wfile.write(bytes([byte]))
                    handler.wfile.flush()
                    time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    break
            return None

        with peer(drip) as (url, requests):
            model = OllamaModel(url)
            model.reset()
            started = time.perf_counter()
            result = model.reply([], [], started + 0.3)
            self.assertTrue(result["deadline_limited"])
            self.assertEqual(result["failure_kind"], "deadline")
            self.assertLess(time.perf_counter() - started, 1.5)
            model.reset()
            result = model.reply([], [], time.perf_counter() + 2)
            self.assertEqual(result["text"], "Synthetic answer")
            self.assertFalse(result["deadline_limited"])
            self.assertEqual(model.metadata["reset_count"], 2)
            self.assertEqual([path for path, _ in requests].count("/api/chat"), 2)

    def test_prefill_stall_is_also_bounded(self):
        def stall(handler, path, body, value):
            if path == "/api/chat":
                time.sleep(0.6)
            return value

        with peer(stall) as (url, requests):
            model = OllamaModel(url)
            started = time.perf_counter()
            result = model.reply([], [], started + 0.2)
            self.assertTrue(result["deadline_limited"])
            self.assertLess(time.perf_counter() - started, 1)

    def test_rejected_empty_or_invalid_api_does_not_become_an_answer(self):
        for response in (
            {"error": "private-body"},
            chat_response(message={"role": "assistant", "content": "", "thinking": "private"}),
            chat_response(done=False),
            chat_response(eval_count=-1),
            chat_response(model="another-model"),
        ):

            def reject(handler, path, body, value, response=response):
                return response if path == "/api/chat" else value

            with self.subTest(response=response), peer(reject) as (url, requests):
                model = OllamaModel(url)
                result = model.reply([], [], time.perf_counter() + 2)
                self.assertIn("failure_kind", result)
                self.assertEqual(result["text"], "")
                self.assertNotIn("private", json.dumps(result))

    def test_http_error_body_and_request_response_size_are_bounded(self):
        def reject(handler, path, body, value):
            handler.send_response(400)
            handler.end_headers()
            handler.wfile.write(b"private-error")
            return None

        with peer(reject) as (url, requests):
            with self.assertRaisesRegex(OllamaError, "^http_error$"):
                request_json(url, "/api/chat", {}, time.perf_counter() + 2)
            with self.assertRaisesRegex(OllamaError, "request_too_large"):
                request_json(
                    url, "/api/chat", {"text": "x" * MAX_REQUEST_BYTES}, time.perf_counter() + 2
                )
            self.assertEqual(len(requests), 1)
        with peer(lambda *args: {"large": "x" * MAX_RESPONSE_BYTES}) as (url, requests):
            with self.assertRaisesRegex(OllamaError, "response_too_large"):
                request_json(url, "/api/version", None, time.perf_counter() + 2)

    def test_unload_must_be_confirmed_and_expired_request_never_connects(self):
        with peer(lambda handler, path, body, value: {} if path == "/api/generate" else value) as (
            url,
            requests,
        ):
            model = OllamaModel(url)
            with self.assertRaisesRegex(OllamaError, "model_unload_unconfirmed"):
                model.reset()
            count = len(requests)
            result = model.reply([], [], time.perf_counter() - 1)
            self.assertTrue(result["deadline_limited"])
            self.assertEqual(len(requests), count)


if __name__ == "__main__":
    unittest.main()
