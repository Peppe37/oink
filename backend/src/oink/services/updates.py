"""New versions: Oink compares itself with the latest GitHub release. «Install and restart»
needs the optional updater sidecar (docker-compose.yml, profile `updater`): Oink writes a
request in `data/updater/`, the updater pulls the new image and recreates the container,
and the database migrates itself at the next start. Without the sidecar the app shows the
two commands to run instead."""

from __future__ import annotations

import json
import logging
import os
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx

from oink.core.config import Settings

log = logging.getLogger("oink.updates")
CACHE_SECONDS = 6 * 3600
ALIVE_SECONDS = 120
_cache: dict[str, Any] = {}


def current_version() -> str:
    env = os.environ.get("OINK_VERSION", "")
    if env and env != "dev":
        return env.lstrip("v")
    try:
        return version("oink")
    except PackageNotFoundError:
        return "0.0.0"


def parse(v: str) -> tuple[int, ...]:
    parts = []
    for piece in v.lstrip("v").split("-")[0].split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts)


async def latest(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> dict[str, Any] | None:
    if not settings.update_check:
        return None
    cached = _cache.get(settings.update_repo)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return dict(cached[1])
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        r = await client.get(
            f"https://api.github.com/repos/{settings.update_repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("update check failed: %s", type(exc).__name__)
        return None
    finally:
        if own:
            await client.aclose()
    found = {
        "version": str(data.get("tag_name", "")).lstrip("v"),
        "notes": str(data.get("body") or "")[:4000],
        "url": str(data.get("html_url") or ""),
        "published": data.get("published_at"),
    }
    _cache[settings.update_repo] = (time.monotonic(), found)
    return dict(found)


def updater_alive(folder: Path) -> bool:
    beat = folder / "alive"
    try:
        return time.time() - int(beat.read_text().strip() or 0) < ALIVE_SECONDS
    except (OSError, ValueError):
        return False


def _write(path: Path, data: dict[str, Any]) -> None:
    """Write by replacing: the updater (root) owns the old file, Oink owns the folder."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def request_update(folder: Path, target: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / "request.json", {"version": target, "at": int(time.time())})
    _write(folder / "status.json", {"state": "requested", "version": target})


def update_state(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads((folder / "status.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


async def status(settings: Settings) -> dict[str, Any]:
    current = current_version()
    found = await latest(settings)
    newer = bool(found and found["version"] and parse(found["version"]) > parse(current))
    return {
        "current": current,
        "latest": found,
        "update_available": newer,
        "can_install": updater_alive(settings.updater_dir),
        "state": update_state(settings.updater_dir),
    }
