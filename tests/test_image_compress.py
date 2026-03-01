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
        # Create a large image (6000x4000 at high quality → likely > 3.75 MB)
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


class TestDetectMimeFromBytes:
    """Unit tests for ContextBuilder._detect_mime_from_bytes."""

    def test_detect_png(self):
        """PNG magic bytes should be detected."""
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(png_header) == "image/png"

    def test_detect_jpeg(self):
        """JPEG magic bytes should be detected."""
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(jpeg_header) == "image/jpeg"

    def test_detect_gif87a(self):
        """GIF87a magic bytes should be detected."""
        gif_header = b"GIF87a" + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(gif_header) == "image/gif"

    def test_detect_gif89a(self):
        """GIF89a magic bytes should be detected."""
        gif_header = b"GIF89a" + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(gif_header) == "image/gif"

    def test_detect_webp(self):
        """WebP magic bytes should be detected."""
        webp_header = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(webp_header) == "image/webp"

    def test_detect_bmp(self):
        """BMP magic bytes should be detected."""
        bmp_header = b"BM" + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(bmp_header) == "image/bmp"

    def test_unknown_returns_none(self):
        """Unknown content should return None."""
        assert ContextBuilder._detect_mime_from_bytes(b"\x00\x01\x02\x03") is None

    def test_riff_non_webp_returns_none(self):
        """RIFF container that is not WebP should return None."""
        riff_avi = b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 100
        assert ContextBuilder._detect_mime_from_bytes(riff_avi) is None

    def test_real_png_file(self, tmp_path: Path):
        """A real PNG file should be detected as image/png."""
        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        path = tmp_path / "test.png"
        img.save(path, format="PNG")
        data = path.read_bytes()
        assert ContextBuilder._detect_mime_from_bytes(data) == "image/png"

    def test_real_jpeg_file(self, tmp_path: Path):
        """A real JPEG file should be detected as image/jpeg."""
        img = Image.new("RGB", (10, 10), color=(0, 255, 0))
        path = tmp_path / "test.jpg"
        img.save(path, format="JPEG")
        data = path.read_bytes()
        assert ContextBuilder._detect_mime_from_bytes(data) == "image/jpeg"


class TestMimeCorrectionInBuildUserContent:
    """Test that _build_user_content corrects MIME type mismatches."""

    def _make_builder(self, tmp_path: Path) -> ContextBuilder:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "AGENTS.md").write_text("test")
        return ContextBuilder(ws)

    def test_png_saved_as_jpg_corrected(self, tmp_path: Path):
        """A PNG file saved with .jpg extension should be sent with image/png MIME."""
        builder = self._make_builder(tmp_path)
        # Create a PNG image but save with .jpg extension (simulates Feishu behavior)
        img = Image.new("RGB", (50, 50), color=(0, 0, 255))
        path = tmp_path / "feishu_image.jpg"
        img.save(path, format="PNG")  # Save as PNG but with .jpg extension!

        result = builder._build_user_content("describe this", [str(path)])
        assert isinstance(result, list)
        # The image_url should have the correct PNG MIME type
        image_item = result[0]
        assert image_item["type"] == "image_url"
        url = image_item["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")  # NOT image/jpeg!

    def test_jpeg_with_correct_extension_unchanged(self, tmp_path: Path):
        """A JPEG file with .jpg extension should keep image/jpeg MIME."""
        builder = self._make_builder(tmp_path)
        img = Image.new("RGB", (50, 50), color=(255, 0, 0))
        path = tmp_path / "photo.jpg"
        img.save(path, format="JPEG")

        result = builder._build_user_content("describe this", [str(path)])
        assert isinstance(result, list)
        url = result[0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")


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
