#!/usr/bin/env python3
"""Prepare, encode, inspect, and stage files for the Missyou/5D Holo fan.

The encoder targets the app-generated EE37/F1 format currently confirmed by
the included playable samples. SD-card staging is kept as a separate step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

HEADER_SIZE = 812  # confirmed against the APK's exported HEADER_DATA object
ALIGNMENT = 28_800  # confirmed on the app-generated 00_TEST.bin sample
BLOCK_SIZE = 1_600  # repeating body unit observed in app-generated output
MAGIC_FAMILIES = {
    b"\xee\x37": "app-generated EE37 family",
    b"\xee\x31": "bundled SD-card EE31 family (legacy/different revision)",
}
PROFILE = {"lights": 116, "lines": 520, "color_depth": 6}
FRAME_RATE = 20
DISPLAY_BYTES_PER_LINE = 44
DISPLAY_BYTES_PER_FRAME = PROFILE["lines"] * PROFILE["color_depth"] * DISPLAY_BYTES_PER_LINE
AUDIO_BYTES_PER_FRAME = 1_600
COLOR_LUT = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000001"
    "0101010101010101010101020202020202020203030303030304040404040405"
    "05050606060707080809090a0a0b0c0c0d0d0e0e0f1011111213141516171819"
    "1a1b1c1d1d1e1f2021222324252628292b2c2d2f31333537383a3b3d3e3f4042"
    "4446484a4d4f515355585a5d606265676a6c6f7275787b7e8285888c8f92969a"
    "9da0a4a7aaadb0b4b7babec1c4c7cacdd0d3d6d9dcdee0e2e4e6e8eaeceeeff0"
    "f1f2f3f4f5f6f7f8f9f9fafafbfbfcfcfdfdfdfefefefeffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_prefix(path: Path, count: int) -> bytes:
    with path.open("rb") as f:
        return f.read(count)


def inspect(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    with path.open("rb") as f:
        first = f.read(HEADER_SIZE)
        f.seek(0)
        signature = f.read(2)
        if size > HEADER_SIZE:
            f.seek(HEADER_SIZE)
            body_head = f.read(BLOCK_SIZE)
        else:
            body_head = b""
        # Report zero-padding only when it is a contiguous all-zero suffix.
        f.seek(0, os.SEEK_END)
        end = f.tell()
        zero_padding = 0
        while end > HEADER_SIZE:
            start = max(HEADER_SIZE, end - 1024 * 1024)
            f.seek(start)
            chunk = f.read(end - start)
            i = len(chunk) - 1
            while i >= 0 and chunk[i] == 0:
                i -= 1
            zero_padding += len(chunk) - 1 - i
            end = start + i + 1
            if i >= 0:
                break
    family = MAGIC_FAMILIES.get(signature, "unrecognized signature")
    result = {
        "path": str(path),
        "size_bytes": size,
        "sha256": sha256(path),
        "signature_hex": signature.hex(" "),
        "family": family,
        "header_bytes_available": len(first),
        "header_sha256": hashlib.sha256(first).hexdigest(),
        "header_matches_sample": None,
        "length_multiple_of_28800": size % ALIGNMENT == 0,
        "length_remainder_28800": size % ALIGNMENT,
        "body_after_812_bytes": max(0, size - HEADER_SIZE),
        "body_1600_byte_units": max(0, size - HEADER_SIZE) // BLOCK_SIZE,
        "body_remainder_1600": max(0, size - HEADER_SIZE) % BLOCK_SIZE,
        "trailing_zero_bytes_after_header": zero_padding,
        "body_first_32_hex": body_head[:32].hex(" "),
    }
    return result


def format_report(result: dict[str, Any]) -> str:
    lines = [
        f"File: {result['path']}",
        f"Size: {result['size_bytes']:,} bytes",
        f"SHA-256: {result['sha256']}",
        f"Signature: {result['signature_hex']} — {result['family']}",
        f"Header: {result['header_bytes_available']} bytes; SHA-256 {result['header_sha256']}",
        f"Length % 28,800: {result['length_remainder_28800']} (whole-file alignment: {result['length_multiple_of_28800']})",
        f"After 812-byte header: {result['body_after_812_bytes']:,} bytes",
        f"  1,600-byte units: {result['body_1600_byte_units']:,}, remainder {result['body_remainder_1600']}",
        f"  trailing zero bytes after header: {result['trailing_zero_bytes_after_header']:,}",
        f"Body start: {result['body_first_32_hex']}",
    ]
    return "\n".join(lines)


def compare(paths: list[Path]) -> list[dict[str, Any]]:
    results = [inspect(p) for p in paths]
    # Compare header to first selected file. Useful when comparing an app export
    # with bundled sample files; it intentionally does not imply compatibility.
    reference = read_prefix(paths[0], HEADER_SIZE) if paths else b""
    for item, p in zip(results, paths):
        item["header_matches_first_file"] = read_prefix(p, HEADER_SIZE) == reference
    return results


def js_round(value: float) -> int:
    """Match JavaScript Math.round for finite values used by the app."""
    return math.floor(value + 0.5)


def even_dimension(value: float) -> int:
    """Round like the app, then add one when the result is odd."""
    result = js_round(value)
    if result < 1:
        raise ValueError("scaled output dimensions must be positive")
    return result if result % 2 == 0 else result + 1


def ffmpeg_filter(width: int, height: int, hflip: bool, vflip: bool) -> str:
    filters = [f"scale={width}:{height}:flags=lanczos"]
    if hflip:
        filters.append("hflip")
    if vflip:
        filters.append("vflip")
    filters.extend([
        "unsharp=3:3:1.2",
        "eq=saturation=1.4",
        "curves=all='0/0 0.08/0.18 0.25/0.28 0.75/0.82 1/1'",
        "fps=20",
        "format=bgr24",
    ])
    return ",".join(filters)


def prepare_video(
    source: Path,
    output: Path,
    scale: float,
    center_x: float | None = None,
    center_y: float | None = None,
    start_sec: float = 0,
    duration: float | None = None,
    hflip: bool = False,
    vflip: bool = False,
) -> dict[str, Any]:
    """Run the Android app's video-to-BGR24 FFmpeg preprocessing on Linux."""
    source = source.expanduser().resolve(strict=True)
    output = output.expanduser().resolve(strict=False)
    if not source.is_file():
        raise ValueError("source must be an existing video file")
    if source == output:
        raise ValueError("source and output must be different files")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    numeric = (scale, start_sec) + tuple(v for v in (center_x, center_y) if v is not None)
    if not all(math.isfinite(v) for v in numeric) or (duration is not None and not math.isfinite(duration)):
        raise ValueError("scale, center, start, and duration values must be finite")
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    if start_sec < 0:
        raise ValueError("start time cannot be negative")
    if duration is not None and duration <= 0:
        raise ValueError("duration must be greater than zero")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FileNotFoundError("ffmpeg and ffprobe are required (install the ffmpeg package)")
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(source)],
        check=False, capture_output=True, text=True,
    )
    if probe.returncode:
        raise ValueError(f"ffprobe could not read the video: {probe.stderr.strip()}")
    try:
        streams = json.loads(probe.stdout).get("streams", [])
        input_width = int(streams[0]["width"])
        input_height = int(streams[0]["height"])
    except (ValueError, IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("ffprobe did not report a video width and height") from exc

    output_width = even_dimension(input_width * scale)
    output_height = even_dimension(input_height * scale)
    if center_x is None:
        center_x = input_width / 2
    if center_y is None:
        center_y = input_height / 2
    output_center_x = js_round(center_x * scale)
    output_center_y = js_round(center_y * scale)
    output.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".bgr", dir=output.parent)
    os.close(fd)
    filter_arg = ffmpeg_filter(output_width, output_height, hflip, vflip)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start_sec:
        command += ["-ss", f"{start_sec:.9g}"]
    if duration is not None:
        command += ["-t", f"{duration:.9g}"]
    command += [
        "-i", str(source), "-vf", filter_arg, "-r", "20", "-an",
        "-pix_fmt", "bgr24", "-f", "rawvideo", temp_name,
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"ffmpeg preprocessing failed: {result.stderr.strip()}")
        os.link(temp_name, output)
        size_bytes = output.stat().st_size
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass

    return {
        "input": str(source),
        "output": str(output),
        "input_width": input_width,
        "input_height": input_height,
        "width": output_width,
        "height": output_height,
        "center_x": output_center_x,
        "center_y": output_center_y,
        "scale": scale,
        "start_sec": start_sec,
        "duration_sec": duration,
        "fps": 20,
        "pixel_format": "bgr24",
        "frame_bytes": output_width * output_height * 3,
        "size_bytes": size_bytes,
        "hflip": hflip,
        "vflip": vflip,
    }


def probe_video_size(source: Path) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FileNotFoundError("ffprobe is required (install the ffmpeg package)")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(source)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise ValueError(f"ffprobe could not read the video: {result.stderr.strip()}")
    try:
        stream = json.loads(result.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (ValueError, IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("ffprobe did not report a video width and height") from exc


def auto_fit_scale(width: int, height: int, center_x: float | None = None, center_y: float | None = None) -> float:
    """Fit the source's centered image area inside the 115-pixel fan radius."""
    cx = width / 2 if center_x is None else center_x
    cy = height / 2 if center_y is None else center_y
    available_radius = min(cx, width - cx, cy, height - cy)
    if available_radius <= 0:
        raise ValueError("center must be inside the source image")
    return (PROFILE["lights"] - 1) / available_radius


def encode_prepared_video(
    raw_path: Path,
    output: Path,
    width: int,
    height: int,
    center_x: float,
    center_y: float,
    rotation_degrees: float = 0,
) -> dict[str, Any]:
    """Encode prepared BGR24 frames into the tested EE37/F1 layout."""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("NumPy is required for encoding (install it with: sudo pacman -S python-numpy)") from exc

    raw_path = raw_path.expanduser().resolve(strict=True)
    output = output.expanduser().resolve(strict=False)
    if not raw_path.is_file():
        raise ValueError("prepared input must be a regular BGR24 file")
    if raw_path == output:
        raise ValueError("input and output must be different files")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if width < 2 or height < 2 or not all(math.isfinite(v) for v in (center_x, center_y, rotation_degrees)):
        raise ValueError("frame dimensions and center must be valid finite values")
    frame_bytes = width * height * 3
    raw_size = raw_path.stat().st_size
    if not frame_bytes or raw_size == 0 or raw_size % frame_bytes:
        raise ValueError(f"BGR24 file size must contain complete {width}x{height} frames")
    frame_count = raw_size // frame_bytes
    if frame_count > 20 * 60 * 60:
        raise ValueError("input exceeds the one-hour safety limit")

    angles = (np.arange(PROFILE["lines"], dtype=np.float64) * (2 * math.pi / PROFILE["lines"]))
    angles += math.radians(rotation_degrees)
    radius = np.arange(PROFILE["lights"] - 1, -1, -1, dtype=np.float64)
    # Worker mapping is radial, with one angle per line. The image-coordinate
    # orientation is exposed as a rotation option because the fan's physical
    # blade zero-angle can vary across mounting orientation.
    map_x = center_x + np.cos(angles)[:, None] * radius[None, :]
    map_y = center_y + np.sin(angles)[:, None] * radius[None, :]
    x0_raw = np.floor(map_x).astype(np.int64)
    y0_raw = np.floor(map_y).astype(np.int64)
    dx = (map_x - x0_raw).astype(np.float32)
    dy = (map_y - y0_raw).astype(np.float32)
    x0 = np.clip(x0_raw, 0, width - 1)
    y0 = np.clip(y0_raw, 0, height - 1)
    x1 = np.clip(x0_raw + 1, 0, width - 1)
    y1 = np.clip(y0_raw + 1, 0, height - 1)
    lut = np.frombuffer(COLOR_LUT, dtype=np.uint8)
    header = read_prefix(Path(__file__).resolve().parent / "protocol" / "ee37-header.bin", HEADER_SIZE)
    if len(header) != HEADER_SIZE:
        raise ValueError("known-good app sample header is missing or incomplete")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    try:
        with raw_path.open("rb") as src, open(temp_name, "wb") as dst:
            dst.write(header)
            for _ in range(frame_count):
                frame = np.frombuffer(src.read(frame_bytes), dtype=np.uint8).reshape(height, width, 3)
                # Native interpolation truncates to an integer before applying
                # its 256-entry brightness table. Input is BGR; output byte
                # order is R, B, G, as established by the RGB-step app export.
                interp = (
                    frame[y0, x0].astype(np.float32) * ((1 - dx) * (1 - dy))[..., None]
                    + frame[y0, x1].astype(np.float32) * (dx * (1 - dy))[..., None]
                    + frame[y1, x0].astype(np.float32) * ((1 - dx) * dy)[..., None]
                    + frame[y1, x1].astype(np.float32) * (dx * dy)[..., None]
                ).astype(np.uint8)
                mapped = lut[interp][:, :, (2, 0, 1)].reshape(PROFILE["lines"], -1)
                for line in mapped:
                    for depth in range(PROFILE["color_depth"]):
                        bits = ((line >> (7 - depth)) & 1).astype(np.uint8)
                        dst.write(np.packbits(bits, bitorder="big").tobytes())
                dst.write(b"\x80" * AUDIO_BYTES_PER_FRAME)
            length = dst.tell()
            dst.write(b"\x00" * ((-length) % ALIGNMENT))
            dst.flush()
            os.fsync(dst.fileno())
        os.link(temp_name, output)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return {
        "input": str(raw_path), "output": str(output), "frames": frame_count,
        "fps": FRAME_RATE, "profile": "F1/fallback (116 lights, 520 lines, 6-bit)",
        "display_bytes_per_frame": DISPLAY_BYTES_PER_FRAME,
        "audio_bytes_per_frame": AUDIO_BYTES_PER_FRAME,
        "size_bytes": output.stat().st_size, "sha256": sha256(output),
        "signature_hex": read_prefix(output, 2).hex(" "),
        "rotation_degrees": rotation_degrees,
    }


def convert_video(
    source: Path,
    output: Path,
    scale: float | str = "auto",
    center_x: float | None = None,
    center_y: float | None = None,
    start_sec: float = 0,
    duration: float | None = None,
    hflip: bool = False,
    vflip: bool = False,
    rotation_degrees: float = 180,
) -> dict[str, Any]:
    """Preprocess a video and encode an EE37/F1 file in one local command."""
    source = source.expanduser().resolve(strict=True)
    output = output.expanduser().resolve(strict=False)
    input_width, input_height = probe_video_size(source)
    if scale == "auto":
        scale_value = auto_fit_scale(input_width, input_height, center_x, center_y)
    else:
        try:
            scale_value = float(scale)
        except (TypeError, ValueError) as exc:
            raise ValueError("scale must be a positive number or 'auto'") from exc
    with tempfile.TemporaryDirectory(prefix="holoctl-") as temp_dir:
        raw = Path(temp_dir) / "frames.bgr"
        prepared = prepare_video(
            source, raw, scale_value, center_x, center_y, start_sec, duration, hflip, vflip,
        )
        result = encode_prepared_video(
            raw, output, prepared["width"], prepared["height"],
            prepared["center_x"], prepared["center_y"], rotation_degrees,
        )
    result["scale"] = scale_value
    result["input_width"] = input_width
    result["input_height"] = input_height
    result["width"] = prepared["width"]
    result["height"] = prepared["height"]
    result["center_x"] = prepared["center_x"]
    result["center_y"] = prepared["center_y"]
    result["hflip"] = hflip
    result["vflip"] = vflip
    return result


def stage_file(source: Path, card_mount: Path, name: str | None = None) -> tuple[Path, str]:
    """Copy a .bin file to a mounted card without overwriting, then verify it."""
    source = source.expanduser().resolve(strict=True)
    card_mount = card_mount.expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".bin":
        raise ValueError("source must be an existing .bin file")
    if not card_mount.is_dir() or not os.path.ismount(card_mount):
        raise ValueError("destination must be the mounted SD-card root (use findmnt to confirm it)")
    target_name = name or source.name
    if Path(target_name).name != target_name or target_name in (".", "..") or not target_name.lower().endswith(".bin"):
        raise ValueError("destination name must be a plain filename ending in .bin")
    target = card_mount / target_name
    if source == target.resolve(strict=False):
        raise ValueError("source and destination are the same file")

    digest = hashlib.sha256()
    copied = False
    try:
        with source.open("rb") as src, target.open("xb") as dst:
            copied = True
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        expected = digest.hexdigest()
        actual = sha256(target)
        if actual != expected:
            raise OSError("SHA-256 mismatch after copy; remove the incomplete destination and retry")
        try:
            dir_fd = os.open(card_mount, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
        return target, actual
    except Exception:
        if copied:
            try:
                target.unlink()
            except OSError:
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(prog="holoctl", description="Prepare, inspect, and safely stage Missyou/5D Holo fan files")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("inspect", help="fingerprint one .bin file")
    one.add_argument("file", type=Path)
    one.add_argument("--json", action="store_true", help="print machine-readable JSON")
    many = sub.add_parser("compare", help="compare signatures and headers across files")
    many.add_argument("files", nargs="+", type=Path)
    many.add_argument("--json", action="store_true", help="print machine-readable JSON")
    stage = sub.add_parser("stage", help="copy a .bin file to the mounted SD-card root without overwriting")
    stage.add_argument("file", type=Path, help="source .bin file")
    stage.add_argument("sd_card_mount", type=Path, help="mounted SD-card root, confirmed with findmnt")
    stage.add_argument("--name", help="new filename on the card; defaults to the source filename")
    prep = sub.add_parser("prepare", help="preprocess video into the app's 20 fps BGR24 frame stream")
    prep.add_argument("video", type=Path, help="source video")
    prep.add_argument("output", type=Path, help="new raw frame file to create")
    prep.add_argument("--scale", type=float, default=1.0, help="editor scale multiplier (default: 1.0)")
    prep.add_argument("--center-x", type=float, help="editor center X in source pixels (default: source midpoint)")
    prep.add_argument("--center-y", type=float, help="editor center Y in source pixels (default: source midpoint)")
    prep.add_argument("--start", type=float, default=0, help="clip start time in seconds (default: 0)")
    prep.add_argument("--duration", type=float, help="clip length in seconds (default: through end of video)")
    prep.add_argument("--hflip", action="store_true", help="apply the app's horizontal flip")
    prep.add_argument("--vflip", action="store_true", help="apply the app's vertical flip")
    prep.add_argument("--json", action="store_true", help="print machine-readable preprocessing metadata")
    conv = sub.add_parser("convert", help="convert a video to an SD-card .bin for the tested EE37/F1 fan profile")
    conv.add_argument("video", type=Path, help="source video")
    conv.add_argument("output", type=Path, help="new .bin file to create")
    conv.add_argument("--scale", default="auto", help="resize multiplier, or auto-fit the image to the fan (default: auto)")
    conv.add_argument("--center-x", type=float, help="editor center X in source pixels (default: source midpoint)")
    conv.add_argument("--center-y", type=float, help="editor center Y in source pixels (default: source midpoint)")
    conv.add_argument("--start", type=float, default=0, help="clip start time in seconds")
    conv.add_argument("--duration", type=float, help="clip length in seconds")
    conv.add_argument("--rotation-deg", type=float, default=180, help="rotate the radial map in degrees (default: 180)")
    conv.add_argument("--hflip", action="store_true", help="flip source horizontally before conversion")
    conv.add_argument("--vflip", action="store_true", help="flip source vertically before conversion")
    conv.add_argument("--json", action="store_true", help="print machine-readable conversion metadata")
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            result = inspect(args.file)
            print(json.dumps(result, indent=2) if args.json else format_report(result))
        elif args.command == "compare":
            results = compare(args.files)
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                ref = results[0]
                print(f"Reference header: {ref['path']} ({ref['signature_hex']})")
                for item in results:
                    same = "same" if item["header_matches_first_file"] else "different"
                    print(f"{item['path']}: {item['size_bytes']:,} bytes; {item['signature_hex']} ({item['family']}); header {same}")
        elif args.command == "prepare":
            result = prepare_video(
                args.video, args.output, args.scale, args.center_x, args.center_y,
                args.start, args.duration, args.hflip, args.vflip,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Prepared BGR24 frames: {result['output']}")
                print(f"Input: {result['input_width']}x{result['input_height']}; output: {result['width']}x{result['height']} at {result['fps']} fps")
                print(f"Scaled center: ({result['center_x']}, {result['center_y']}); frame size: {result['frame_bytes']:,} bytes")
                print(f"Raw file: {result['size_bytes']:,} bytes ({result['size_bytes'] // result['frame_bytes']} complete frames)")
        elif args.command == "convert":
            if args.output.suffix.lower() != ".bin":
                raise ValueError("output filename must end in .bin")
            result = convert_video(
                args.video, args.output, args.scale, args.center_x, args.center_y,
                args.start, args.duration, args.hflip, args.vflip, args.rotation_deg,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Created EE37/F1 fan video: {result['output']}")
                print(f"Frames: {result['frames']} at {FRAME_RATE} fps; size: {result['size_bytes']:,} bytes")
                print(f"Scale: {result['scale']:.6g}; frame: {result['width']}x{result['height']}; rotation: {result['rotation_degrees']:g}°")
                print(f"SHA-256: {result['sha256']}")
                print("This file is for SD-card testing; keep the fan switched off while changing the card.")
        else:
            target, digest = stage_file(args.file, args.sd_card_mount, args.name)
            print(f"Copied and verified: {target}")
            print(f"SHA-256: {digest}")
            print("Safely eject the SD card before removing it.")
    except OSError as exc:
        print(f"holoctl: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"holoctl: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
