"""Narrow pinned-CLI control routing; ordinary queries continue using serve."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .exceptions import ErrorKind, FailurePhase, NinebotError
from .vehicle_cache import native_rows, read_cache

COMMANDS = {
    "bell": "bell",
    "buck": "buck",
    "engine/start": "engine-start",
    "engine/stop": "engine-stop",
}
MAX_ROUTES = 64
_CODE = re.compile(r"control: server code=(-?[0-9]{1,7}) message=", re.ASCII)


@dataclass(frozen=True)
class ControlRoute:
    observed_at: datetime
    state: Literal["missing", "known", "ambiguous"]
    business_line: Literal["ebike", "motor"] | None
    matching_rows: int

    @property
    def native(self) -> bool:
        return self.business_line == "motor"

    def diagnostics(self) -> dict[str, str | int | None]:
        return {
            "source": "native_vehicle_cache_at_command",
            "observed_at": self.observed_at.isoformat(),
            "state": self.state,
            "business_line": self.business_line,
            "matching_rows": self.matching_rows,
            "transport": "native_cli" if self.native else "serve",
        }


def control_route(directory: Path, sn: str) -> ControlRoute:
    rows = [row for row in native_rows(read_cache(directory)) if row["wnumber"] == sn]
    lines = {row["business_line"] for row in rows}
    line = next(iter(lines)) if len(lines) == 1 else None
    return ControlRoute(
        datetime.now(UTC),
        "known" if line else "ambiguous" if lines else "missing",
        line,
        len(rows),
    )


def control_failure(diagnostic: bytes) -> NinebotError:
    """Interpret only reviewed native prefixes/codes, discard all free text."""
    message = diagnostic[:1024].decode("utf-8", errors="replace").splitlines()
    first = message[0].removeprefix("Error: ") if message else ""
    if first.startswith("decrypt failed:"):
        first = f"control: {first}"
    match = _CODE.match(first)
    error = NinebotError(ErrorKind.SERVICE)
    error.at_phase(FailurePhase.NATIVE_CONTROL)
    error.observe_response(
        None,
        {"error": {"upstream_code": match[1] if match else None, "message": first}},
    )
    return error
