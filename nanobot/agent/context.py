"""Context builder for assembling agent prompts."""

import base64
import io
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

## nanobot Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": self._build_runtime_context(channel, chat_id)},
            {"role": "user", "content": self._build_user_content(current_message, media)},
        ]

    # Maximum raw image size in bytes before compression is applied.
    # LLM APIs typically limit base64-encoded payloads to 5 MB.  Since base64
    # inflates data by ~4/3, the raw file threshold is 5 MB × 3/4 ≈ 3.75 MB.
    IMAGE_MAX_BYTES = 3_750_000  # ~3.75 MB → ≤ 5 MB after base64

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images.

        Images larger than :pyattr:`IMAGE_MAX_BYTES` (~3.75 MB) are
        automatically compressed so the base64-encoded result stays within
        the 5 MB LLM API limit.
        """
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            raw = p.read_bytes()
            # Detect actual MIME type from file content (magic bytes) to avoid
            # mismatch when file extension is wrong (e.g. Feishu saves PNG as .jpg).
            detected_mime = self._detect_mime_from_bytes(raw)
            if detected_mime:
                if detected_mime != mime:
                    logger.debug("MIME correction for {}: {} → {}", p.name, mime, detected_mime)
                mime = detected_mime
            if len(raw) > self.IMAGE_MAX_BYTES:
                raw, mime = self._compress_image(raw, mime, p.name)
            b64 = base64.b64encode(raw).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    # ── image MIME detection helpers ──────────────────────────────

    # Magic-byte signatures for common image formats.
    _MAGIC_SIGS: list[tuple[bytes, str]] = [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF", "image/webp"),  # WebP starts with RIFF....WEBP
        (b"BM", "image/bmp"),
    ]

    @staticmethod
    def _detect_mime_from_bytes(data: bytes) -> str | None:
        """Detect image MIME type from file content magic bytes.

        Returns the detected MIME string, or ``None`` if unrecognized.
        """
        for sig, mime in ContextBuilder._MAGIC_SIGS:
            if data[:len(sig)] == sig:
                # Extra check for WebP: bytes 8-12 must be "WEBP"
                if sig == b"RIFF" and data[8:12] != b"WEBP":
                    continue
                return mime
        return None

    # ── image compression helpers ──────────────────────────────────

    @staticmethod
    def _compress_image(
        data: bytes,
        mime: str,
        filename: str = "<unknown>",
        *,
        target_bytes: int | None = None,
        max_dimension: int = 2048,
        min_quality: int = 30,
    ) -> tuple[bytes, str]:
        """Compress an image to fit within *target_bytes*.

        Strategy:
        1. Resize so the longest side ≤ *max_dimension* (preserving aspect ratio).
        2. Encode as JPEG with progressively lower quality until the result is
           small enough or *min_quality* is reached.

        Returns:
            ``(compressed_bytes, mime_type)`` — mime is always ``image/jpeg``
            after compression.
        """
        if target_bytes is None:
            target_bytes = ContextBuilder.IMAGE_MAX_BYTES

        try:
            from PIL import Image
        except ImportError:
            logger.warning(
                "Pillow not installed — cannot compress image {} ({:.1f} MB). "
                "Install with: pip install Pillow",
                filename,
                len(data) / 1024 / 1024,
            )
            return data, mime

        original_size = len(data)
        try:
            img = Image.open(io.BytesIO(data))
        except Exception:
            logger.warning("Failed to open image {} for compression, sending as-is", filename)
            return data, mime

        # Convert palette / RGBA → RGB for JPEG output
        if img.mode in ("RGBA", "LA", "P", "PA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Step 1: resize if larger than max_dimension
        w, h = img.size
        if max(w, h) > max_dimension:
            ratio = max_dimension / max(w, h)
            new_w, new_h = int(w * ratio), int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.debug("Resized {} from {}×{} to {}×{}", filename, w, h, new_w, new_h)

        # Step 2: encode with decreasing quality until target met
        quality = 85
        while quality >= min_quality:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            result = buf.getvalue()
            if len(result) <= target_bytes:
                logger.info(
                    "Compressed image {} from {:.1f} MB to {:.1f} MB (quality={}, {}×{})",
                    filename,
                    original_size / 1024 / 1024,
                    len(result) / 1024 / 1024,
                    quality,
                    img.size[0],
                    img.size[1],
                )
                return result, "image/jpeg"
            quality -= 10

        # Best effort: return the lowest quality result even if still above target
        logger.warning(
            "Image {} compressed to {:.1f} MB (quality={}) but still above {:.1f} MB target",
            filename,
            len(result) / 1024 / 1024,
            min_quality,
            target_bytes / 1024 / 1024,
        )
        return result, "image/jpeg"
    
    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}

        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages
