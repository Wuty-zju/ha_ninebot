"""Real disposable synthetic peer; no weights, inference, HA or provider credentials."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.assistant_model_host import ReusableCompletion


class ReusableHostTests(unittest.TestCase):
    def peer(self, directory, mode="normal"):
        binary, log = Path(directory) / "fixture-engine", Path(directory) / "requests.jsonl"
        binary.write_text(
            f"#!{sys.executable}\n"
            "import json,os,sys,time\n"
            "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
            "assert 'HF_TOKEN' not in os.environ and 'LLAMA_ARG_AGENT' not in os.environ\n"
            "assert '--no-agent' in sys.argv and '--no-webui' in sys.argv\n"
            "class Peer(BaseHTTPRequestHandler):\n"
            " def log_message(self,*args): pass\n"
            " def do_GET(self):\n"
            '  self.send_response(200);self.end_headers();self.wfile.write(b\'{"status":"ok"}\')\n'
            " def do_POST(self):\n"
            "  assert self.headers['Authorization']=='Bearer '+os.environ['LLAMA_API_KEY']\n"
            "  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))\n"
            f"  with open({str(log)!r},'a') as f:f.write(json.dumps(data)+'\\n')\n"
            f"  if {mode!r}=='stall':time.sleep(20)\n"
            "  result={'content':'synthetic answer','stop_type':'eos','truncated':False,"
            "'tokens_evaluated':3,'tokens_predicted':2,'tokens_cached':0}\n"
            f"  if {mode!r}=='invalid':result['tokens_cached']=True\n"
            "  self.send_response(200);self.end_headers()\n"
            "  self.wfile.write(json.dumps(result).encode())\n"
            "HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Peer).serve_forever()\n"
        )
        binary.chmod(0o700)
        return binary, log

    def test_reuses_one_owned_process_and_resets_cache_between_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            binary, log = self.peer(directory)
            host = ReusableCompletion(binary, Path("synthetic-weights"))
            try:
                with patch.dict("os.environ", {"LLAMA_ARG_AGENT": "true", "HF_TOKEN": "canary"}):
                    first = host.complete("synthetic prompt", time.perf_counter() + 5)
                process = host.process
                second = host.complete("synthetic prompt continued", time.perf_counter() + 5)
                self.assertIs(host.process, process)
                host.reset()
                host.complete("new case", time.perf_counter() + 5)
                rows = [json.loads(row) for row in log.read_text().splitlines()]
                self.assertEqual([row["cache_prompt"] for row in rows], [False, True, False])
                self.assertEqual(first["text"], second["text"])
                self.assertFalse(first["deadline_limited"])
                self.assertEqual(first["engine_cached_tokens"], 0)
            finally:
                host.close()
            self.assertIsNotNone(process.poll())

    def test_deadline_reaps_a_stalled_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            binary, _ = self.peer(directory, "stall")
            host = ReusableCompletion(binary, Path("synthetic-weights"))
            children = []
            original = subprocess.Popen

            def spawn(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                return child

            with patch("scripts.assistant_model_host.subprocess.Popen", side_effect=spawn):
                result = host.complete("synthetic", time.perf_counter() + 0.5)
            self.assertTrue(result["deadline_limited"])
            self.assertIsNone(host.process)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())

    def test_invalid_counters_close_without_exposing_response_text(self):
        with tempfile.TemporaryDirectory() as directory:
            binary, _ = self.peer(directory, "invalid")
            host = ReusableCompletion(binary, Path("synthetic-weights"))
            with self.assertRaisesRegex(RuntimeError, "^Evaluation completion process failed$"):
                host.complete("synthetic", time.perf_counter() + 5)
            self.assertIsNone(host.process)

    def test_expired_call_does_not_spawn_or_contact_a_peer(self):
        host = ReusableCompletion(Path("unused"), Path("unused"))
        with patch("scripts.assistant_model_host.subprocess.Popen") as spawn:
            self.assertTrue(host.complete("synthetic", time.perf_counter() - 1)["deadline_limited"])
        spawn.assert_not_called()

    def test_closed_host_cannot_spawn_a_late_process(self):
        host = ReusableCompletion(Path("unused"), Path("unused"))
        host.close()
        with patch("scripts.assistant_model_host.subprocess.Popen") as spawn:
            with self.assertRaises(RuntimeError):
                host.complete("synthetic", time.perf_counter() + 1)
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
