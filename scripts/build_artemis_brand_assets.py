from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SMALL_ICON_DARKEN = {16: 0.64, 20: 0.71, 24: 0.77, 32: 0.84, 40: 0.91, 48: 0.95}
SMALL_ICON_SHARPEN = {
    16: (0.48, 175),
    20: (0.52, 150),
    24: (0.56, 130),
    32: (0.62, 105),
    40: (0.66, 80),
    48: (0.70, 65),
}


def _alpha_crop(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("源图完全透明，无法生成应用图标。")
    return rgba.crop(bbox)


def _visible_subject_crop(image: Image.Image, threshold: int) -> Image.Image:
    rgba = image.convert("RGBA")
    visible_alpha = rgba.getchannel("A").point(lambda value: 255 if value >= threshold else 0)
    bbox = visible_alpha.getbbox()
    if bbox is None:
        return _alpha_crop(rgba)
    return rgba.crop(bbox)


def _premultiplied_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _fit_center(image: Image.Image, canvas_size: int, occupied_size: int) -> Image.Image:
    scale = min(occupied_size / image.width, occupied_size / image.height)
    target = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    resized = _premultiplied_resize(image, target)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    offset = ((canvas_size - target[0]) // 2, (canvas_size - target[1]) // 2)
    canvas.alpha_composite(resized, offset)
    return canvas


def _enhance_small_frame(frame: Image.Image, size: int) -> Image.Image:
    radius, percent = SMALL_ICON_SHARPEN[size]
    enhanced = frame.convert("RGBa").filter(
        ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2)
    ).convert("RGBA")

    darken = SMALL_ICON_DARKEN[size]
    pixels = []
    for red, green, blue, alpha in enhanced.getdata():
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if alpha >= 36 and luminance <= 92:
            red = round(red * darken)
            green = round(green * darken)
            blue = round(blue * darken)
        pixels.append((red, green, blue, alpha))
    enhanced.putdata(pixels)
    return enhanced


def _build_frame(cropped: Image.Image, size: int) -> Image.Image:
    if size <= 24:
        optical_source = _visible_subject_crop(cropped, 64)
    elif size <= 64:
        optical_source = _visible_subject_crop(cropped, 32)
    else:
        optical_source = _visible_subject_crop(cropped, 16)

    occupied = size - 1 if size <= 64 else max(1, round(size * 0.985))
    frame = _fit_center(optical_source, size, occupied)
    if size <= 48:
        frame = _enhance_small_frame(frame, size)
    return frame


def _checkerboard(size: tuple[int, int], cell: int = 12) -> Image.Image:
    board = Image.new("RGBA", size, "#f8fafc")
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#dfe7ef")
    return board


def _build_preview(frame_16: Image.Image, frame_32: Image.Image) -> Image.Image:
    preview = _checkerboard((640, 320), 16)
    draw = ImageDraw.Draw(preview)
    font = ImageFont.load_default()
    panels = ((frame_16, 12, "16 px · 12x"), (frame_32, 6, "32 px · 6x"))
    for index, (frame, scale, label) in enumerate(panels):
        zoomed = frame.resize((frame.width * scale, frame.height * scale), Image.Resampling.NEAREST)
        panel_x = 32 + index * 304
        x = panel_x + (256 - zoomed.width) // 2
        y = 42
        preview.alpha_composite(zoomed, (x, y))
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        draw.rounded_rectangle((panel_x + 66, 264, panel_x + 190, 294), radius=8, fill="#ffffffdd")
        draw.text((panel_x + (256 - text_width) // 2, 274), label, fill="#173127", font=font)
    return preview


def build_assets(source: Path, output_dir: Path, icon_output: Path) -> None:
    source_image = Image.open(source).convert("RGBA")
    cropped = _alpha_crop(source_image)

    output_dir.mkdir(parents=True, exist_ok=True)
    icon_output.parent.mkdir(parents=True, exist_ok=True)

    master = _fit_center(cropped, 1024, 1000)
    master_path = output_dir / "artemis_symbol_1024.png"
    master.save(master_path, format="PNG", optimize=True)

    frames = {size: _build_frame(cropped, size) for size in ICON_SIZES}
    frames[16].save(output_dir / "artemis_symbol_16.png", format="PNG", optimize=True)
    frames[32].save(output_dir / "artemis_symbol_32.png", format="PNG", optimize=True)

    preview = _build_preview(frames[16], frames[32])
    preview.save(output_dir / "artemis_symbol_preview_16_32.png", format="PNG", optimize=True)

    largest = frames[256]
    append_images = [frames[size] for size in ICON_SIZES if size != 256]
    largest.save(
        icon_output,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=append_images,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Artemis 品牌 PNG、ICO 与小尺寸预览。")
    parser.add_argument("source", type=Path, help="原始透明 PNG")
    parser.add_argument("output_dir", type=Path, help="PNG 与预览输出目录")
    parser.add_argument("icon_output", type=Path, help="多尺寸 ICO 输出路径")
    args = parser.parse_args()
    build_assets(args.source, args.output_dir, args.icon_output)


if __name__ == "__main__":
    main()
