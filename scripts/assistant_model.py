"""Pinned CPU-only test model; no server, provider key or product dependency."""

import argparse
import hashlib
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

MODEL = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
REVISION = "a06e946bb6b655725eafa393f4a9745d460374c9"
WEIGHT_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
WEIGHT_SHA = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"
TOKENIZER = "Qwen/Qwen3-4B-Instruct-2507"
TOKENIZER_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
ENGINE_TAG = "b12182"
ENGINE_COMMIT = "fd27b2034905f8dadd8bd6d2cc44595f4929d391"
ENGINE_SHA = "c1dbfdb9cbef7ee50b6ab08142d6424184d68b242906837770439ed118c57d99"
ENGINE_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    "b12182/llama-b12182-bin-ubuntu-x64.tar.gz"
)
CONTEXT_TOKENS = 8192
MAX_TOKENS = 256


def verify_file(path: Path, expected: str) -> None:
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError("Evaluation asset checksum mismatch")


def prepare(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "engine.tar.gz"
    with urlopen(ENGINE_URL, timeout=60) as response, archive.open("wb") as stream:
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 64 * 1024 * 1024:
                raise ValueError("Evaluation engine exceeds its download bound")
            stream.write(block)
    verify_file(archive, ENGINE_SHA)
    with tarfile.open(archive) as bundle:
        if sum(member.size for member in bundle.getmembers()) > 256 * 1024 * 1024:
            raise ValueError("Evaluation engine exceeds its extraction bound")
        bundle.extractall(directory, filter="data")
    archive.unlink()
    binaries = list(directory.rglob("llama-completion"))
    if len(binaries) != 1 or not binaries[0].is_file():
        raise ValueError("Expected one fixed completion executable")
    binary = binaries[0].resolve()
    probe = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
    if probe.returncode or not re.search(r"\b12182\b", probe.stdout + probe.stderr):
        raise ValueError("Fixed completion engine is not executable on this runner")
    return binary


def expired_result(started: float) -> dict:
    return {
        "text": "",
        "input_tokens": None,
        "output_tokens": None,
        "seconds": time.perf_counter() - started,
        "length_limited": None,
        "deadline_limited": True,
    }


def complete(binary: Path, weights: Path, prompt: str, deadline: float) -> dict:
    """Kill the single completion process at the case deadline, including prefill."""
    started = time.perf_counter()
    remaining = deadline - started
    if remaining <= 0:
        return expired_result(started)
    with NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt") as stream:
        stream.write(prompt)
        stream.flush()
        command = [
            str(binary),
            "-m",
            str(weights),
            "-f",
            stream.name,
            "-n",
            str(MAX_TOKENS),
            "-c",
            str(CONTEXT_TOKENS),
            "-t",
            "2",
            "--temp",
            "0",
            "--seed",
            "0",
            "--no-display-prompt",
            "--no-conversation",
            "--no-warmup",
            "--color",
            "off",
            "-ngl",
            "0",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(0.001, deadline - time.perf_counter()),
                check=True,
            )
        except subprocess.TimeoutExpired:
            return expired_result(started)
        except (subprocess.CalledProcessError, OSError):
            raise RuntimeError("Evaluation completion process failed") from None
    if len(result.stdout.encode()) > 16 * 1024:
        raise ValueError("Evaluation completion exceeds its output bound")
    # completion.cpp emits the EOG line, then two final newlines (lines929/949).
    marker = " [end of text]\n\n\n"
    terminal_marker = result.stdout.endswith(marker)
    text = result.stdout.removesuffix(marker)
    counts = {}
    for key, pattern in (
        ("engine_prompt_tokens", r"prompt eval time\s*=\s*[\d.]+ ms\s*/\s*(\d+) tokens"),
        ("engine_decoded_tokens", r":\s+eval time\s*=\s*[\d.]+ ms\s*/\s*(\d+) runs"),
    ):
        match = re.search(pattern, result.stderr)
        counts[key] = int(match[1]) if match else None
    return {
        "text": text,
        "engine_terminal_marker": terminal_marker,
        "seconds": time.perf_counter() - started,
        "length_limited": None,
        "deadline_limited": False,
        **counts,
    }


class CpuModel:
    def __init__(self, host: str = "process") -> None:
        if host not in {"process", "reusable"}:
            raise ValueError("Unsupported evaluation host")
        from huggingface_hub import hf_hub_download, snapshot_download
        from transformers import AutoTokenizer

        self.binary = Path(os.environ["NINEBOT_EVAL_LLAMA"])
        server = self.binary.with_name("llama-server")
        if host == "reusable" and not server.is_file():
            raise ValueError("Fixed engine bundle has no reusable server")
        self.weights = Path(hf_hub_download(MODEL, WEIGHT_FILE, revision=REVISION, token=False))
        verify_file(self.weights, WEIGHT_SHA)
        tokenizer_path = snapshot_download(
            TOKENIZER,
            revision=TOKENIZER_REVISION,
            token=False,
            allow_patterns=[
                "tokenizer*.json",
                "config.json",
                "merges.txt",
                "vocab.json",
                "LICENSE",
            ],
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_path, local_files_only=True, trust_remote_code=False
        )
        self.metadata = {
            "engine": "llama.cpp",
            "engine_tag": ENGINE_TAG,
            "engine_commit": ENGINE_COMMIT,
            "engine_asset_sha256": ENGINE_SHA,
            "weight_file": WEIGHT_FILE,
            "weight_sha256": WEIGHT_SHA,
            "tokenizer": TOKENIZER,
            "tokenizer_revision": TOKENIZER_REVISION,
            "context_tokens": CONTEXT_TOKENS,
            "token_count_basis": "official tokenizer: rendered prompt and visible output",
            "completion_host": host,
            "host_lifecycle": "cohort" if host == "reusable" else "turn",
            "first_reply_includes_startup": True,
        }
        with self.binary.open("rb") as stream:
            self.metadata["engine_executable_sha256"] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
        self._host = None
        if host == "reusable":
            from scripts.assistant_model_host import ReusableCompletion

            self._host = ReusableCompletion(server, self.weights)
            with server.open("rb") as stream:
                self.metadata["server_executable_sha256"] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()

    def reset(self) -> None:
        if self._host is not None:
            self._host.reset()

    def close(self) -> None:
        if self._host is not None:
            self._host.close()

    def reply(self, messages: list[dict], tools: list[dict], deadline: float) -> dict:
        started = time.perf_counter()
        if started >= deadline:
            return expired_result(started)
        prompt = self.tokenizer.apply_chat_template(
            messages, tools=tools or None, add_generation_prompt=True, tokenize=False
        )
        input_tokens = len(self.tokenizer.encode(prompt, add_special_tokens=False))
        if input_tokens + MAX_TOKENS + 64 > CONTEXT_TOKENS:
            return {
                "text": "",
                "input_tokens": input_tokens,
                "output_tokens": 0,
                "seconds": time.perf_counter() - started,
                "length_limited": False,
                "deadline_limited": False,
                "context_limited": True,
            }
        result = (
            self._host.complete(prompt, deadline)
            if self._host is not None
            else complete(self.binary, self.weights, prompt, deadline)
        )
        if not result["deadline_limited"]:
            result["input_tokens"] = input_tokens
            result["output_tokens"] = len(
                self.tokenizer.encode(result["text"], add_special_tokens=False)
            )
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", type=Path, required=True)
    binary = prepare(parser.parse_args().prepare)
    if output := os.environ.get("GITHUB_ENV"):
        with Path(output).open("a") as stream:
            stream.write(f"NINEBOT_EVAL_LLAMA={binary}\n")
    print(f"Prepared fixed {ENGINE_TAG} CPU completion engine")
