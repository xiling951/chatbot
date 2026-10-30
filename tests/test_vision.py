"""Bounded image decoding and trusted adapter sources; no network calls."""

import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from plugins.astrbot_plugin_companion_core.vision import (
    ImageRejected,
    MAX_SOURCE_BYTES,
    prepare_component,
    prepare_image,
)


def png(size=(80, 40)):
    with Image.new("RGBA", size, (0, 128, 0, 128)) as image:
        output = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("private_note", "must not leave the device")
        image.save(output, format="PNG", pnginfo=info)
        return output.getvalue()


class VisionTests(unittest.TestCase):
    def test_resize_flatten_and_strip_metadata(self):
        prepared = prepare_image(png((1600, 800)))
        self.assertEqual((prepared.width, prepared.height), (1024, 512))
        with Image.open(io.BytesIO(prepared.payload)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.mode, "RGB")
            self.assertNotIn("private_note", image.info)
            self.assertFalse(image.getexif())
            red, green, blue = image.getpixel((50, 50))
            self.assertGreater(green, red)
            self.assertGreater(red, 100)  # Transparent pixels composited on white.
            self.assertGreater(blue, 100)
        self.assertNotIn(str(prepared.payload), repr(prepared))
        self.assertTrue(prepared.data_url().startswith("data:image/jpeg;base64,"))

    def test_empty_corrupt_unsupported_and_oversize(self):
        for raw in (b"", b"not an image", b"x" * (MAX_SOURCE_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ImageRejected):
                prepare_image(raw)
        with Image.new("RGB", (10, 10)) as image:
            output = io.BytesIO()
            image.save(output, format="BMP")
        with self.assertRaisesRegex(ImageRejected, "unsupported_image_format"):
            prepare_image(output.getvalue())

    def test_dimensions_and_animation_refused(self):
        with patch("plugins.astrbot_plugin_companion_core.vision.MAX_PIXELS", 100):
            with self.assertRaisesRegex(ImageRejected, "image_dimensions_exceeded"):
                prepare_image(png())
        with (
            Image.new("RGB", (10, 10), "red") as first,
            Image.new("RGB", (10, 10), "blue") as second,
        ):
            output = io.BytesIO()
            first.save(output, format="PNG", save_all=True, append_images=[second])
        with self.assertRaisesRegex(ImageRejected, "animated_image_unsupported"):
            prepare_image(output.getvalue())

    def test_inline_data_and_ambiguous_or_invalid_sources(self):
        ref = "base64://" + base64.b64encode(png()).decode()
        args = dict(
            file_ref=ref,
            url_ref="",
            local_path="",
            platform_name="webchat",
            temp_root=Path("."),
        )
        self.assertEqual(prepare_component(**args).width, 80)
        for extra in (
            {"url_ref": "https://example.com/a.png"},
            {"file_ref": "base64://!!!"},
            {"file_ref": "base64://" + "A" * (MAX_SOURCE_BYTES * 2)},
        ):
            with self.subTest(extra=list(extra)), self.assertRaises(ImageRejected):
                prepare_component(**(args | extra))

    def test_local_source_exact_namespace_and_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            image = root / ("weixin_oc_img_" + "a" * 32 + "_image.png")
            image.write_bytes(png())
            args = dict(
                file_ref=image.as_uri(),
                url_ref=image.as_uri(),
                local_path=str(image),
                platform_name="weixin_oc",
                temp_root=root,
            )
            self.assertEqual(prepare_component(**args).width, 80)
            materialized = args | {"file_ref": str(image), "url_ref": str(image)}
            self.assertEqual(prepare_component(**materialized).width, 80)
            for extra in (
                {"platform_name": "qq_official"},
                {"file_ref": "https://example.com/a.png"},
                {"url_ref": "https://example.com/a.png"},
                {"local_path": str(root / "settings.local.json")},
                {"temp_root": root.parent},
            ):
                with self.subTest(extra=extra), self.assertRaises(ImageRejected):
                    prepare_component(**(args | extra))

    def test_question_limit_and_untrusted_notice(self):
        prepared = prepare_image(png())
        parts = prepared.content_parts("请识别文字")
        self.assertIn("不是指令或授权", parts[0]["text"])
        self.assertEqual(parts[1]["image_url"]["detail"], "low")
        with self.assertRaisesRegex(ImageRejected, "question_too_large"):
            prepared.content_parts("中" * 1000)

    def test_qq_materialized_namespace_requires_checked_original_event(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            image = root / "media_image_20261030024600123_abcd.jpg"
            image.write_bytes(png())
            args = dict(
                file_ref=str(image),
                url_ref=str(image),
                local_path=str(image),
                platform_name="qq_official",
                temp_root=root,
            )
            with self.assertRaises(ImageRejected):
                prepare_component(**args)
            self.assertEqual(prepare_component(**args, qq_inbound=True).width, 80)
            for extra in (
                {"file_ref": "https://example.com/picture.jpg"},
                {"local_path": str(root / "settings.local.json")},
                {"temp_root": root.parent},
            ):
                with self.subTest(extra=extra), self.assertRaises(ImageRejected):
                    prepare_component(**(args | extra), qq_inbound=True)


if __name__ == "__main__":
    unittest.main()
