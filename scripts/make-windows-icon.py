from __future__ import annotations

import struct
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_png(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"Not a PNG file: {path}")
    width, height = struct.unpack(">II", data[16:24])
    if width > 256 or height > 256:
        raise ValueError(f"ICO PNG frame is too large: {path} ({width}x{height})")
    return width, height, data


def build_ico(frames: list[tuple[int, int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(frames))
    directory = bytearray()
    payload = bytearray()
    offset = 6 + 16 * len(frames)

    for width, height, data in frames:
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                0 if width == 256 else width,
                0 if height == 256 else height,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        payload.extend(data)
        offset += len(data)

    return header + bytes(directory) + bytes(payload)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    icon_dir = repo_root / "apps" / "desktop" / "src-tauri" / "icons"
    source_paths = [
        icon_dir / "32x32.png",
        icon_dir / "128x128.png",
        icon_dir / "128x128@2x.png",
    ]
    frames = [read_png(path) for path in source_paths]
    output = icon_dir / "icon.ico"
    output.write_bytes(build_ico(frames))
    print(f"Generated Windows icon: {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
