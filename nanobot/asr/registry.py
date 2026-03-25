"""ASR plugin registry — §76.

Scans ``~/.nanobot/plugins/asr/`` for JSON registration files and loads
the first enabled engine.  Provides an ``async recognize()`` method that
calls the registered script via subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger


class ASRRegistry:
    """Registry for ASR engine plugins.

    Parameters
    ----------
    plugins_dir:
        Directory to scan for ``*.json`` registration files.
        Typically ``~/.nanobot/plugins/asr/``.
    """

    def __init__(self, plugins_dir: Path) -> None:
        self._engine: dict[str, Any] | None = None
        self._load(plugins_dir)

    # ── Loading ─────────────────────────────────────────────────────

    def _load(self, plugins_dir: Path) -> None:
        """Scan *plugins_dir* for the first enabled ASR engine config."""
        if not plugins_dir.is_dir():
            logger.debug("ASR plugins dir not found: {}", plugins_dir)
            return

        for json_file in sorted(plugins_dir.glob("*.json")):
            try:
                config = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("ASR: failed to read {}: {}", json_file.name, exc)
                continue

            if config.get("enabled"):
                self._engine = config
                logger.info("ASR engine loaded: {} (from {})",
                            config.get("engine", "?"), json_file.name)
                break  # use the first enabled engine

        if self._engine is None:
            logger.debug("No enabled ASR engine found in {}", plugins_dir)

    # ── Public API ──────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Return ``True`` if an ASR engine is loaded and ready."""
        return self._engine is not None

    async def recognize(self, audio_path: str, duration_ms: int) -> dict[str, Any] | None:
        """Call the ASR script and return the result.

        Parameters
        ----------
        audio_path:
            Local filesystem path to the audio file.
        duration_ms:
            Audio duration in milliseconds (informational, passed to script).

        Returns
        -------
        dict or None
            ``{"recognition": "...", "engine": "..."}`` on success,
            ``None`` on failure or if no engine is loaded.
        """
        if not self._engine:
            return None

        script = os.path.expanduser(self._engine["script"])
        timeout = self._engine.get("timeout", 30)

        if not os.path.isfile(script):
            logger.warning("ASR script not found: {}", script)
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, script,
                "--file-path", audio_path,
                "--duration", str(duration_ms),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )

            if stderr:
                logger.debug("ASR stderr: {}", stderr.decode(errors="replace")[:500])

            if proc.returncode == 0:
                result = json.loads(stdout.decode(errors="replace"))
                logger.info("ASR recognition OK (engine={}): {}",
                            result.get("engine", "?"),
                            result.get("recognition", "")[:80])
                return result
            else:
                logger.warning("ASR script failed (exit {}): {}",
                               proc.returncode,
                               stderr.decode(errors="replace")[:200])
                return None

        except asyncio.TimeoutError:
            logger.warning("ASR script timed out ({}s)", timeout)
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            return None
        except json.JSONDecodeError as exc:
            logger.warning("ASR script returned invalid JSON: {}", exc)
            return None
        except Exception as exc:
            logger.warning("ASR error: {}", exc)
            return None
