"""Asset integrity and real process deadline without a model installation."""

import hashlib
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.assistant_model import complete, verify_file


class CpuHostTests(unittest.TestCase):
    def test_asset_bytes_must_match_the_fixed_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "engine"
            path.write_bytes(b"synthetic-engine")
            verify_file(path, hashlib.sha256(path.read_bytes()).hexdigest())
            path.write_bytes(b"changed-engine")
            with self.assertRaises(ValueError):
                verify_file(path, hashlib.sha256(b"synthetic-engine").hexdigest())

    def test_expired_deadline_never_starts_another_process(self):
        with patch("scripts.assistant_model.subprocess.run") as run:
            result = complete(Path("binary"), Path("weights"), "prompt", time.perf_counter() - 1)
        self.assertTrue(result["deadline_limited"])
        self.assertIsNone(result["output_tokens"])
        run.assert_not_called()

    def test_prefill_timeout_kills_the_completion_process(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "fixture-engine"
            binary.write_text("#!/bin/sh\nexec /bin/sleep 20\n")
            binary.chmod(0o700)
            children = []
            popen = subprocess.Popen

            def start(*args, **kwargs):
                child = popen(*args, **kwargs)
                children.append(child)
                return child

            with patch("scripts.assistant_model.subprocess.Popen", side_effect=start):
                result = complete(
                    binary, Path("weights"), "synthetic prompt", time.perf_counter() + 0.5
                )
            self.assertTrue(result["deadline_limited"])
            self.assertEqual(result["text"], "")
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            with self.assertRaises(ProcessLookupError):
                os.kill(children[0].pid, 0)

    def test_engine_counters_do_not_confuse_prompt_and_decode(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            stdout="synthetic answer",
            stderr=(
                "llama_perf_context_print: prompt eval time = 20.00 ms / 123 tokens\n"
                "llama_perf_context_print:        eval time = 10.00 ms / 7 runs\n"
            ),
        )
        with patch("scripts.assistant_model.subprocess.run", return_value=result) as run:
            data = complete(Path("binary"), Path("weights"), "prompt", time.perf_counter() + 2)
        self.assertEqual(data["engine_prompt_tokens"], 123)
        self.assertEqual(data["engine_decoded_tokens"], 7)
        command = run.call_args.args[0]
        self.assertIn("--no-conversation", command)
        self.assertNotIn("--server", command)
        self.assertIsNone(data["length_limited"])

    def test_engine_error_text_is_not_exposed(self):
        with (
            patch(
                "scripts.assistant_model.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, [], stderr="private error"),
            ),
            self.assertRaisesRegex(RuntimeError, "^Evaluation completion process failed$"),
        ):
            complete(Path("binary"), Path("weights"), "prompt", time.perf_counter() + 2)

    def test_only_the_exact_engine_terminal_suffix_is_removed(self):
        for raw, expected, removed in (
            ("answer [end of text]\n", "answer", True),
            (
                "[end of text] is quoted in this answer",
                "[end of text] is quoted in this answer",
                False,
            ),
            ("answer [end of text]", "answer [end of text]", False),
            ("answer", "answer", False),
        ):
            with (
                self.subTest(raw=raw),
                patch(
                    "scripts.assistant_model.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, stdout=raw, stderr=""),
                ),
            ):
                data = complete(Path("binary"), Path("weights"), "prompt", time.perf_counter() + 2)
            self.assertEqual(data["text"], expected)
            self.assertEqual(data["engine_terminal_marker"], removed)
