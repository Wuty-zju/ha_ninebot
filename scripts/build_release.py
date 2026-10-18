"""Build and verify HACS packages from Git objects, never candidate Python code."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PREFIX = "custom_components/ninebot/"
FILENAME = "ninebot.zip"
MAX_BYTES = 64 * 1024 * 1024


def git_bytes(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=data, capture_output=True, check=False
    )
    if result.returncode:
        raise ValueError("Required Git objects are unavailable")
    return result.stdout


def committed_files(repo: Path, sha: str) -> dict[str, tuple[int, bytes]]:
    """Load exact tracked blobs in one batch; reject links and generated content."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Use a full commit SHA")
    entries = []
    admitted_bytes = 0
    for row in git_bytes(repo, "ls-tree", "-rlz", sha, "--", PREFIX).split(b"\0"):
        if not row:
            continue
        metadata, path = row.split(b"\t", 1)
        mode, kind, oid, length = metadata.decode().split()
        name = path.decode().removeprefix(PREFIX)
        parts = PurePosixPath(name).parts
        if (
            kind != "blob"
            or mode not in {"100644", "100755"}
            or not path.decode().startswith(PREFIX)
            or not parts
            or name != PurePosixPath(name).as_posix()
            or ".." in parts
            or "\\" in name
            or name.startswith("/")
            or "__pycache__" in parts
            or name.endswith((".pyc", ".pyo"))
        ):
            raise ValueError("Runtime tree contains unsupported paths, links or generated files")
        size = int(length)
        admitted_bytes += size
        if not 0 <= size <= 8 * 1024 * 1024 or admitted_bytes > MAX_BYTES:
            raise ValueError("Git runtime exceeds package admission limits")
        entries.append((name, int(mode, 8) & 0o777, oid))
    if not 1 <= len(entries) <= 2048:
        raise ValueError("Runtime file count is outside package bounds")
    raw = git_bytes(
        repo, "cat-file", "--batch", data="".join(f"{e[2]}\n" for e in entries).encode()
    )
    files = {}
    offset = 0
    total = 0
    for name, mode, oid in entries:
        end = raw.index(b"\n", offset)
        object_id, kind, size_text = raw[offset:end].decode().split()
        size = int(size_text)
        start = end + 1
        stop = start + size
        total += size
        if (
            object_id != oid
            or kind != "blob"
            or not 0 <= size <= 8 * 1024 * 1024
            or total > MAX_BYTES
            or stop >= len(raw)
            or raw[stop : stop + 1] != b"\n"
        ):
            raise ValueError("Git blob framing or package size is invalid")
        files[name] = (mode, raw[start:stop])
        offset = stop + 1
    if offset != len(raw) or "manifest.json" not in files:
        raise ValueError("Package is missing its runtime manifest")
    return files


def verify_package(path: Path, files: dict[str, tuple[int, bytes]]) -> str:
    """Check exact membership, paths, modes and uncompressed bytes against Git."""
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Package exceeds the size limit")
    with ZipFile(path) as archive:
        rows = archive.infolist()
        if len(rows) != len(files) or {r.filename for r in rows} != set(files):
            raise ValueError("Package membership differs from the committed runtime")
        for row in rows:
            mode, expected = files[row.filename]
            if (
                row.file_size != len(expected)
                or row.flag_bits & 1
                or row.external_attr >> 16 != (0o100000 | mode)
                or archive.read(row) != expected
            ):
                raise ValueError("Package member differs from its committed blob")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_package(repo: Path, sha: str, version: str, output: Path) -> dict:
    files = committed_files(repo, sha)
    manifest = json.loads(files["manifest.json"][1])
    hacs = json.loads(git_bytes(repo, "show", f"{sha}:hacs.json"))
    if manifest.get("domain") != "ninebot" or manifest.get("version") != version:
        raise ValueError("Package domain/version does not match the release request")
    if hacs.get("zip_release") is not True or hacs.get("filename") != FILENAME:
        raise ValueError("The candidate must enable zip_release and filename ninebot.zip")
    output.mkdir(parents=True, exist_ok=True)
    path = output / FILENAME
    if path.resolve().is_relative_to((repo / PREFIX).resolve()):
        raise ValueError("Do not write build output inside the runtime tree")
    with ZipFile(path, "w") as archive:
        for name, (mode, content) in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=9)
    digest = verify_package(path, files)
    (output / "SHA256SUMS").write_text(f"{digest}  {FILENAME}\n", encoding="utf-8")
    result = {
        "candidate_sha": sha,
        "version": version,
        "tag": f"v{version}",
        "runtime_tree_sha": git_bytes(repo, "rev-parse", f"{sha}:custom_components/ninebot")
        .decode()
        .strip(),
        "filename": FILENAME,
        "sha256": digest,
        "bytes": path.stat().st_size,
        "members": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, (_, data) in sorted(files.items())
        ],
    }
    (output / "package-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_package(Path.cwd(), args.sha, args.version, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "members"}, indent=2))


if __name__ == "__main__":
    main()
