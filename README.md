# Holo Fan Linux

Linux command-line tools for converting videos into SD-card `.bin` files for the Missyou / 5D Holo fan. The converter runs locally and does not require the phone app or a network connection.

This is an unofficial community project and is not affiliated with or endorsed by Missyou.

> **Experimental hardware support:** Playback has been confirmed by the project maintainer on one Missyou 12.6-inch fan. The encoder targets the observed `EE 37` format and measured F1/fallback profile. Other fan models and firmware may use different formats.

## Features

- Convert a video to the tested fan `.bin` format.
- Inspect `.bin` headers, sizes, alignment, and padding.
- Stage a `.bin` onto a mounted SD card without overwriting an existing file; verify the copy with SHA-256.
- Accept video formats that the installed FFmpeg can decode, including MP4, MOV, and animated GIF.
- Use `--scale auto` to fit the source frame to the converter's measured radial display area.

Audio is not copied. Output files contain silent audio slots, and conversion uses 20 frames per second. A black video background is treated as unlit on the fan, so dark backgrounds usually work best.

## Install

Requirements: Python 3.10 or newer, FFmpeg with `ffprobe`, and NumPy.

On EndeavourOS or Arch Linux:

```sh
sudo pacman -S ffmpeg python-numpy
```

On Debian or Ubuntu:

```sh
sudo apt install ffmpeg python3-numpy
```

## Convert a video

Place your video in this folder, then run:

```sh
python3 holoctl.py convert input.mp4 ARASA.bin --duration 6
```

`auto` is the default scale. For other formats, replace `input.mp4` with the media filename, for example `input.mov` or `input.gif`. For an animated GIF, set `--duration` to the portion you want to use. A still image, even if named `.gif`, has no animation to preserve.

To adjust the size manually, specify a scale multiplier. For a 480×480 video, `auto` is about `0.48`, producing a 230×230 frame that fits the measured fan mapping. Values that produce frames much smaller than that may cause edge sampling; `1.0` can crop the outside of the source image. Keep the whole design centered and leave a little black margin around it.

```sh
python3 holoctl.py convert input.mp4 SMALL.bin --scale 0.40 --duration 6
python3 holoctl.py convert input.mp4 TURN.bin --rotation-deg 0 --duration 6
```

The default `--rotation-deg 180` matched the included mapping probe best. Try `0`, `90`, or `270` if the fan displays the orientation differently. Other options include `--center-x`, `--center-y`, `--start`, `--hflip`, and `--vflip`. Conversion refuses to overwrite an existing output file.

## Inspect and copy to the SD card

Inspect the generated file and identify the card's mount point:

```sh
python3 holoctl.py inspect ARASA.bin
findmnt -t vfat,exfat
```

Use the mount point reported on your computer:

```sh
python3 holoctl.py stage ARASA.bin '/run/media/your-user/CARD_LABEL' --name ARASA.bin
```

`stage` writes to the mounted filesystem's root, refuses to replace a file, and verifies the copy by SHA-256. Keep a backup of the original card files. Safely eject the card and switch the fan off before changing it.

## Other commands

`inspect` fingerprints a `.bin`. `compare` checks signatures and headers across files. `prepare` writes intermediate BGR24 frames without creating a `.bin`:

```sh
python3 holoctl.py compare original.bin ARASA.bin
python3 holoctl.py prepare input.mp4 output.bgr --scale 0.48 --duration 6
```

## Format notes

- The measured profile is 116 radial lights, 520 lines, and 6 color bits.
- The encoder uses an 812-byte `EE 37` compatibility header in `protocol/ee37-header.bin`; it contains no video frames.
- Each display frame occupies 137,280 bytes and is followed by a 1,600-byte silent audio slot.
- Frames are sampled bilinearly from 20 fps BGR24 input and packed as six bit planes. The brightness lookup table and R/B/G output byte order match observed app output.
- Finished files are padded to a multiple of 28,800 bytes.

No video or playable `.bin` examples are included in this release. The 812-byte compatibility header is runtime protocol data, not a playable sample. You can add your own tested examples later.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## License

The project is released under the MIT License. See [LICENSE](LICENSE).
