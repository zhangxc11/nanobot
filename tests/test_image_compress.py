"""Tests for image compression in ContextBuilder."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from nanobot.agent.context import ContextBuilder


def _make_jpeg(tmp_path: Path, name: str, width: int, height: int, quality: int = 95) -> Path:
    """Create a JPEG test image and return its path."""
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    path = tmp_path / name
    img.save(path, format="JPEG", quality=quality)
    return path


def _make_png_rgba(tmp_path: Path, name: str, width: int, height: int) -> Path:
    """Create a PNG RGBA test image."""
    img = Image.new("RGBA", (width, height), color=(0, 255, 0, 128))
    path = tmp_path / name
    img.save(path, format="PNG")
    return path


class TestCompressImage:
    """Unit tests for ContextBuilder._compress_image."""

    def test_small_image_not_compressed(self, tmp_path: Path):
        """Images under the threshold should be returned as-is."""
        path = _make_jpeg(tmp_path, "small.jpg", 100, 100)
        raw = path.read_bytes()
        assert len(raw) < ContextBuilder.IMAGE_MAX_BYTES

        result, mime = ContextBuilder._compress_image(raw, "image/jpeg", "small.jpg")
        # Small image: should still be valid JPEG, size should be similar
        assert mime == "image/jpeg"
        assert len(result) <= ContextBuilder.IMAGE_MAX_BYTES

    def test_large_image_compressed(self, tmp_path: Path):
        """Images over the threshold should be compressed below the target."""
        # Create a large image (6000x4000 at high quality → likely > 5MB)
        img = Image.new("RGB", (6000, 4000))
        # Fill with random-ish pattern to make it harder to compress
        import random
        random.seed(42)
        pixels = img.load()
        for y in range(0, 4000, 10):
            for x in range(0, 6000, 10):
                c = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                for dy in range(min(10, 4000 - y)):
                    for dx in range(min(10, 6000 - x)):
                        pixels[x + dx, y + dy] = c

        path = tmp_path / "large.jpg"
        img.save(path, format="JPEG", quality=98)
        raw = path.read_bytes()

        if len(raw) <= ContextBuilder.IMAGE_MAX_BYTES:
            pytest.skip("Generated image not large enough for compression test")

        result, mime = ContextBuilder._compress_image(raw, "image/jpeg", "large.jpg")
        assert mime == "image/jpeg"
        assert len(result) <= ContextBuilder.IMAGE_MAX_BYTES
        # Verify it's still a valid JPEG
        img_out = Image.open(io.BytesIO(result))
        assert img_out.format == "JPEG"

    def test_rgba_converted_to_rgb(self, tmp_path: Path):
        """RGBA images should be converted to RGB for JPEG output."""
        path = _make_png_rgba(tmp_path, "rgba.png", 200, 200)
        raw = path.read_bytes()

        result, mime = ContextBuilder._compress_image(
            raw, "image/png", "rgba.png", target_bytes=10 * 1024 * 1024
        )
        assert mime == "image/jpeg"
        img_out = Image.open(io.BytesIO(result))
        assert img_out.mode == "RGB"

    def test_resize_large_dimension(self, tmp_path: Path):
        """Images wider/taller than max_dimension should be resized."""
        img = Image.new("RGB", (5000, 3000), color=(0, 0, 255))
        path = tmp_path / "wide.jpg"
        img.save(path, format="JPEG", quality=95)
        raw = path.read_bytes()

        result, mime = ContextBuilder._compress_image(
            raw, "image/jpeg", "wide.jpg", max_dimension=1024
        )
        assert mime == "image/jpeg"
        img_out = Image.open(io.BytesIO(result))
        assert max(img_out.size) <= 1024

    def test_custom_target_bytes(self, tmp_path: Path):
        """Custom target_bytes should be respected."""
        img = Image.new("RGB", (2000, 1500), color=(128, 128, 128))
        path = tmp_path / "medium.jpg"
        img.save(path, format="JPEG", quality=95)
        raw = path.read_bytes()

        target = 50 * 1024  # 50 KB
        result, mime = ContextBuilder._compress_image(
            raw, "image/jpeg", "medium.jpg", target_bytes=target
        )
        assert mime == "image/jpeg"
        assert len(result) <= target


class TestBuildUserContent:
    """Integration tests for _build_user_content with image compression."""

    def _make_builder(self, tmp_path: Path) -> ContextBuilder:
        """Create a minimal ContextBuilder for testing."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "AGENTS.md").write_text("test")
        return ContextBuilder(ws)

    def test_no_media(self, tmp_path: Path):
        """Without media, should return plain text."""
        builder = self._make_builder(tmp_path)
        result = builder._build_user_content("hello", None)
        assert result == "hello"

    def test_small_image_included(self, tmp_path: Path):
        """Small images should be included without compression."""
        builder = self._make_builder(tmp_path)
        path = _make_jpeg(tmp_path, "tiny.jpg", 50, 50)

        result = builder._build_user_content("describe this", [str(path)])
        assert isinstance(result, list)
        assert len(result) == 2  # 1 image + 1 text
        assert result[0]["type"] == "image_url"
        assert result[1]["type"] == "text"
        assert result[1]["text"] == "describe this"

    def test_non_image_file_skipped(self, tmp_path: Path):
        """Non-image files should be skipped."""
        builder = self._make_builder(tmp_path)
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("not an image")

        result = builder._build_user_content("hello", [str(txt_file)])
        assert result == "hello"
