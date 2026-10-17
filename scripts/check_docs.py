"""Offline public-doc links, asset integrity and bilingual outline checks; stdlib only."""

import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAIRS = (
    ("README.md", "README_zh.md"),
    ("docs/guide.md", "docs/guide_zh.md"),
    ("docs/history.md", "docs/history_zh.md"),
    ("docs/migration.md", "docs/migration_zh.md"),
)
TARGET = re.compile(r"\]\((<[^>\n]+>|[^)\n]+)\)")
ATTRIBUTE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']")


def prose(text: str) -> str:
    """Exclude fenced examples without treating YAML comments as headings."""
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1][0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def headings(text: str) -> list[tuple[int, str]]:
    return [(len(m[1]), m[2]) for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*#*$", prose(text), re.M)]


def anchors(text: str) -> set[str]:
    result = set(re.findall(r"(?:id|name)=[\"']([^\"']+)[\"']", prose(text)))
    counts: dict[str, int] = {}
    for _, title in headings(text):
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)
        title = re.sub(r"<[^>]*>", "", title).lower().strip()
        base = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
        count = counts.get(base, 0)
        counts[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def references(text: str) -> list[str]:
    text = prose(text)
    values = [m[1].strip() for m in TARGET.finditer(text)]
    values += ATTRIBUTE.findall(text)
    values += re.findall(r"^\s*\[[^\]]+\]:\s*(\S+)", text, re.M)
    return [
        value[1:-1] if value.startswith("<") and value.endswith(">") else value for value in values
    ]


def link_errors(root: Path, path: Path, text: str) -> list[str]:
    errors = []
    for target in references(text):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            if url.scheme == "file":
                errors.append(f"{path.name}: local file URI is not portable")
            continue  # Never request external URLs or require their availability.
        destination = (path.parent / unquote(url.path)).resolve() if url.path else path
        if not destination.is_relative_to(root.resolve()):
            errors.append(f"{path.name}: reference escapes repository: {target}")
        elif not destination.exists():
            errors.append(f"{path.name}: missing reference: {target}")
        elif url.fragment and destination.suffix == ".md":
            if unquote(url.fragment) not in anchors(destination.read_text()):
                errors.append(f"{path.name}: missing anchor: {target}")
    if re.search(r"/Users/|/home/[^ /]+/|[A-Z]:\\Users\\", text):
        errors.append(f"{path.name}: private workstation path in public documentation")
    return errors


def image_size(content: bytes) -> tuple[str, int, int]:
    """Read PNG/JPEG dimensions without decoding pixels or adding Pillow."""
    if len(content) >= 24 and content[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", content[16:24])
        return ".png", width, height
    if content[:2] == b"\xff\xd8":
        position = 2
        while position + 4 <= len(content):
            if content[position] != 255:
                break
            while position < len(content) and content[position] == 255:
                position += 1
            if position >= len(content):
                break
            marker = content[position]
            position += 1
            if marker in {0xDA, 0xD9}:
                break
            if marker == 1 or 0xD0 <= marker <= 0xD8:
                continue
            size = int.from_bytes(content[position : position + 2])
            if size < 2 or position + size > len(content):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if size < 7:
                    break
                height, width = struct.unpack(">HH", content[position + 3 : position + 7])
                return ".jpg", width, height
            position += size
    raise ValueError("Unsupported/malformed documentation image")


def asset_errors(root: Path) -> list[str]:
    errors = []
    manifest = root / "docs/assets/manifest.json"
    if not manifest.exists():
        return ["Missing documentation asset manifest"]
    data = json.loads(manifest.read_text())
    fixture = root / data["fixture"]
    if not fixture.resolve().is_relative_to(root.resolve()) or not fixture.is_file():
        return ["Asset fixture must belong to this repository"]
    if hashlib.sha256(fixture.read_bytes()).hexdigest() != data["fixture_sha256"]:
        errors.append("Documentation fixture changed: review/re-record assets and provenance")
    declared = set()
    for item in data["assets"]:
        target = (manifest.parent / item["file"]).resolve()
        if target.parent != manifest.parent.resolve() or not target.is_file():
            errors.append("Invalid/missing documentation asset")
            continue
        if item["file"] in declared:
            errors.append("Duplicate documentation asset")
        declared.add(item["file"])
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"] or len(content) != item["bytes"]:
            errors.append(f"Asset digest/size changed: {target.name}")
        try:
            extension, width, height = image_size(content)
            if extension != target.suffix:
                errors.append(f"Asset extension does not match its bytes: {target.name}")
            if (width, height) != (item["width"], item["height"]):
                errors.append(f"Asset dimensions changed: {target.name}")
        except ValueError:
            errors.append(f"Malformed documentation image: {target.name}")
        if item.get("synthetic") is not True or item.get("real_data") is not False:
            errors.append(f"Asset needs reviewed synthetic provenance: {target.name}")
    actual = {p.name for p in manifest.parent.iterdir() if p.suffix in {".png", ".jpg", ".jpeg"}}
    if actual != declared:
        errors.append("Untracked or missing image in documentation asset manifest")
    return errors


def audit(root: Path) -> tuple[list[str], int]:
    """Only inspect public Git files, never a parent workspace/capture tree."""
    tracked = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    # Include new public docs before staging, never ignored private override files.
    paths = {root / name for name in tracked if name.endswith(".md")}
    errors = []
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root.resolve()):
            errors.append("Public Markdown file escapes repository")
        else:
            errors.extend(link_errors(root, path, path.read_text()))
    notice = (root / "NOTICE").read_text()
    for target in re.findall(r"(?:docs|tests)/[^\s]+?\.(?:md|txt)(?:#[\w-]+)?", notice):
        errors.extend(link_errors(root, root / "NOTICE", f"[Notice]({target})"))
    for english, chinese in PAIRS:
        a, b = root / english, root / chinese
        if not a.is_file() or not b.is_file():
            errors.append(f"Missing primary language pair: {english}, {chinese}")
        elif [level for level, _ in headings(a.read_text())] != [
            level for level, _ in headings(b.read_text())
        ]:
            errors.append(f"Bilingual outline differs: {english}, {chinese}")
    errors.extend(asset_errors(root))
    return errors, len(paths)


def main() -> None:
    try:
        errors, count = audit(ROOT)
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Documentation check failed: {type(error).__name__}") from error
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Documentation: {count} Markdown files; links, NOTICE, assets and outlines OK")
    print("External availability, translation meaning and screenshot privacy require human review.")


if __name__ == "__main__":
    main()
