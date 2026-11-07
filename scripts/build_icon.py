"""从可编辑 SVG 生成 PNG 和多尺寸 Windows ICO（使用项目现有依赖）。"""
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image


def main():
    assets = Path(__file__).resolve().parents[1] / "kandu" / "assets"
    with fitz.open(assets / "kandu.svg") as document:
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(4, 4), alpha=True)
    with Image.open(BytesIO(pixmap.tobytes("png"))) as source:
        icon = source.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
    icon.save(assets / "kandu.png", optimize=True)
    for size in (16, 32):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(assets / f"kandu-{size}.png", optimize=True)
    icon.save(assets / "kandu.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("Generated kandu.png and kandu.ico from kandu.svg")


if __name__ == "__main__":
    main()
