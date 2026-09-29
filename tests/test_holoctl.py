import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import holoctl


class ProtocolHeaderTests(unittest.TestCase):
    def test_ee37_compatibility_header_fingerprint(self):
        header = ROOT / "protocol" / "ee37-header.bin"
        data = header.read_bytes()
        self.assertEqual(len(data), holoctl.HEADER_SIZE)
        self.assertEqual(data[:2], b"\xee\x37")
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            "2490260f72703e40982554bc0251bf0cb482a73e2c78b016794a7ba158a5407a",
        )


class VideoPreparationTests(unittest.TestCase):
    def test_dimension_rounding_matches_app_then_forces_even(self):
        self.assertEqual(holoctl.js_round(128.5), 129)
        self.assertEqual(holoctl.even_dimension(128.5), 130)
        self.assertEqual(holoctl.even_dimension(128.4), 128)

    def test_filter_order_matches_android_preprocessing(self):
        filters = holoctl.ffmpeg_filter(128, 130, hflip=True, vflip=True).split(",")
        self.assertEqual(filters[:3], ["scale=128:130:flags=lanczos", "hflip", "vflip"])
        self.assertEqual(filters[-2:], ["fps=20", "format=bgr24"])
        self.assertIn("unsharp=3:3:1.2", filters)
        self.assertIn("eq=saturation=1.4", filters)

    def test_auto_fit_scale_uses_fan_radius(self):
        self.assertAlmostEqual(holoctl.auto_fit_scale(512, 512), 230 / 512)
        self.assertAlmostEqual(holoctl.auto_fit_scale(1920, 1080), 230 / 1080)

    def test_native_brightness_table_has_256_entries(self):
        self.assertEqual(len(holoctl.COLOR_LUT), 256)
        self.assertEqual(holoctl.COLOR_LUT[0], 0)
        self.assertEqual(holoctl.COLOR_LUT[64], 5)
        self.assertEqual(holoctl.COLOR_LUT[255], 255)


class EncoderTests(unittest.TestCase):
    def test_black_frame_uses_known_header_silence_and_padding(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("NumPy is not installed")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "black.bgr"
            out = root / "black.bin"
            raw.write_bytes(bytes(2 * 2 * 3))
            result = holoctl.encode_prepared_video(raw, out, 2, 2, 1, 1)
            data = out.read_bytes()
            self.assertEqual(data[:2], b"\xee\x37")
            self.assertEqual(data[:holoctl.HEADER_SIZE], (ROOT / "protocol" / "ee37-header.bin").read_bytes())
            self.assertEqual(result["frames"], 1)
            self.assertEqual(len(data) % holoctl.ALIGNMENT, 0)
            body = data[holoctl.HEADER_SIZE:]
            self.assertEqual(body[:holoctl.DISPLAY_BYTES_PER_FRAME], bytes(holoctl.DISPLAY_BYTES_PER_FRAME))
            self.assertEqual(body[holoctl.DISPLAY_BYTES_PER_FRAME:holoctl.DISPLAY_BYTES_PER_FRAME + holoctl.AUDIO_BYTES_PER_FRAME], b"\x80" * holoctl.AUDIO_BYTES_PER_FRAME)
            self.assertEqual(data[-1], 0)


class StageFileTests(unittest.TestCase):
    def test_stage_copies_and_verifies_without_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.bin"
            mount = root / "card"
            mount.mkdir()
            source.write_bytes(b"test fan data")
            with patch("holoctl.os.path.ismount", return_value=True):
                target, digest = holoctl.stage_file(source, mount, "MAPTEST.bin")
                self.assertEqual(target.read_bytes(), b"test fan data")
                self.assertEqual(digest, holoctl.sha256(source))
                with self.assertRaises(FileExistsError):
                    holoctl.stage_file(source, mount, "MAPTEST.bin")
                self.assertEqual(target.read_bytes(), b"test fan data")

    def test_stage_refuses_an_unmounted_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.bin"
            source.write_bytes(b"test fan data")
            mount = root / "card"
            mount.mkdir()
            with patch("holoctl.os.path.ismount", return_value=False):
                with self.assertRaisesRegex(ValueError, "mounted SD-card root"):
                    holoctl.stage_file(source, mount)


if __name__ == "__main__":
    unittest.main()
