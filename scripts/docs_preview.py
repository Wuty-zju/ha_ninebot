"""Loopback-only synthetic documentation preview; no HA, cloud or user files."""

import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def branding() -> bytes:
    """Compare the existing packaged icon on light/dark surfaces, unchanged."""
    return b"""<!doctype html><meta charset="utf-8"><title>Brand review</title>
<style>body{font:18px system-ui;margin:32px;background:#ddd;color:#123}
section{display:inline-block;padding:24px;margin:12px;border-radius:12px}
.light{background:#fff}.dark{background:#18232b;color:#fff}img{margin:16px}</style>
<h1>Existing Ninebot integration icon</h1><p>Repository asset, not a new logo.</p>
<section class="light">Light<br><img src="/ninebot-brand/icon.png" width="96" alt="96px">
<img src="/ninebot-brand/icon.png" width="32" alt="32px"></section>
<section class="dark">Dark fallback<br><img src="/ninebot-brand/icon.png" width="96" alt="96px">
<img src="/ninebot-brand/icon.png" width="32" alt="32px"></section>"""


def document(language: str = "en", scale: str = "1") -> bytes:
    """Render the existing offline fixture shell; keep runtime UI unchanged."""
    if language not in {"en", "zh-Hans"} or scale not in {"1", "0.8"}:
        raise ValueError("Unsupported documentation presentation")
    text = (ROOT / "tests/frontend/map-fixture.html").read_text()
    text = text.replace("language: 'zh-Hans'", f"language: '{language}'")
    text = text.replace('lang="zh-CN"', f'lang="{language}"')
    text = text.replace("max-width:850px", f"max-width:1180px;zoom:{scale}")
    text = text.replace(
        "Synthetic vehicles · offline fixture",
        "Ninebot · ride history demo" if language == "en" else "Ninebot · 历史行程演示",
    )
    text = text.replace(
        "No real account or ride data. Map tiles are disabled.",
        "Synthetic data · no account, vehicle or cloud access · map tiles off"
        if language == "en"
        else "合成数据 · 无账号、车辆或云端访问 · 底图关闭",
    )
    return text.encode()


def asset(path: str) -> Path:
    """Only expose packaged frontend assets, never the configuration or repo."""
    if path == "/ninebot-brand/icon.png":
        return ROOT / "custom_components/ninebot/brand/icon.png"
    prefix = "/ninebot-static/"
    if not path.startswith(prefix):
        raise ValueError("Not a preview asset")
    base = ROOT / "custom_components/ninebot/frontend"
    target = (base / path.removeprefix(prefix)).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise ValueError("Not a preview asset")
    return target


class Preview(BaseHTTPRequestHandler):
    """Serve a fixed demo with external connections blocked by CSP."""

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        try:
            if url.path == "/branding":
                body = branding()
                content_type = "text/html; charset=utf-8"
            elif url.path == "/":
                query = parse_qs(url.query)
                body = document(query.get("lang", ["en"])[0], query.get("scale", ["1"])[0])
                content_type = "text/html; charset=utf-8"
            else:
                target = asset(url.path)
                body = target.read_bytes()
                content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        except (OSError, ValueError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid routine browser logs and accidental query-value disclosure."""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    options = parser.parse_args()
    with HTTPServer(("127.0.0.1", options.port), Preview) as server:
        print(
            f"Synthetic preview: http://127.0.0.1:{server.server_port}/?lang=en&scale=0.8",
            flush=True,
        )
        server.serve_forever()
