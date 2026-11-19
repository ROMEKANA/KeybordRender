"""Render a YAML keyboard layout as two PNG images."""

import argparse
from dataclasses import dataclass
from functools import cache
from itertools import combinations
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont
from shapely import Polygon, box
from shapely.affinity import translate
from shapely.errors import GEOSException
from shapely.validation import explain_validity
import yaml


DEFAULT_INPUT = Path("E-YOOSO_Z88_layout_v1.0.5.yaml")
GROUPS = ("main", "navigation", "numpad")
OUTPUTS = {
    "main.png": ("main",),
    "navigation_numpad.png": ("navigation", "numpad"),
}
REQUIRED_FIELDS = set("id label row col x y w h".split())

# All physical distances are in u. The output resolution is in pixels per u.
PIXELS_PER_UNIT = 160
SUPERSAMPLE = 3
MARGIN = 0.12
KEY_GAP = 0.065
CORNER_RADIUS = 0.065
BORDER_WIDTH = 0.011
LABEL_PADDING = 0.075
MAX_FONT_SIZE = 0.57

BACKGROUND = "#000000"
KEY_COLOR = "#424242"
BORDER_COLOR = "#707070"
TEXT_COLOR = "#FFFFFF"


@dataclass(frozen=True)
class Key:
    id: str
    label: str
    row: int
    col: int
    shape: Polygon


def read_number(value, context: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{context} must be a finite number")
    return float(value)


def read_key(data, context: str) -> Key:
    if not isinstance(data, dict):
        raise ValueError(f"{context} must be a mapping")
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"{context} is missing: {', '.join(sorted(missing))}")

    key_id, label = data["id"], data["label"]
    if not isinstance(key_id, str) or not key_id.strip():
        raise ValueError(f"{context}.id must be a nonempty string")
    context = f"{context} ({key_id})"
    if not isinstance(label, str) or not label.strip() or any(not char.isprintable() for char in label):
        raise ValueError(f"{context}.label must be a nonempty, printable string")
    row, col = data["row"], data["col"]
    if any(type(index) is not int or index < 0 for index in (row, col)):
        raise ValueError(f"{context}.row and .col must be nonnegative integers")

    x, y, w, h = [read_number(data[name], f"{context}.{name}") for name in ("x", "y", "w", "h")]
    if w <= 0 or h <= 0:
        raise ValueError(f"{context}.w and .h must be positive")

    shape = box(0, 0, w, h)
    if "outline" in data:
        outline = data["outline"]
        if not isinstance(outline, list) or len(outline) < 3:
            raise ValueError(f"{context}.outline must contain at least three vertices")
        vertices = []
        for index, vertex in enumerate(outline):
            vertex_context = f"{context}.outline[{index}]"
            if not isinstance(vertex, list) or len(vertex) != 2:
                raise ValueError(f"{vertex_context} must be an [x, y] pair")
            vertices.append(tuple(read_number(v, vertex_context) for v in vertex))

        # Accept both open and explicitly closed rings.
        if vertices[0] == vertices[-1]:
            vertices.pop()
        if len(vertices) < 3 or len(set(vertices)) != len(vertices):
            raise ValueError(f"{context}.outline must have at least three distinct, unrepeated vertices")
        shape = Polygon(vertices)
        if not shape.is_valid or shape.area <= 0:
            raise ValueError(f"{context}.outline is invalid: {explain_validity(shape)}")
        if any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
               for a, b in zip(shape.bounds, (0, 0, w, h))):
            raise ValueError(f"{context}.outline must span (0, 0) to (w, h)")

    # Outline points are local to the key. Apply the YAML origin once.
    return Key(key_id, label, row, col, translate(shape, x, y))


def load_layout(path: Path) -> dict[str, list[Key]]:
    with path.open(encoding="utf-8-sig") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict):
        raise ValueError("YAML must contain a groups mapping")

    groups = {}
    ids = set()
    for group in GROUPS:
        entries = data["groups"].get(group)
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"groups.{group} must be a nonempty list")
        keys = []
        for index, entry in enumerate(entries):
            key = read_key(entry, f"groups.{group}[{index}]")
            if key.id in ids:
                raise ValueError(f"Duplicate key id: {key.id}")
            ids.add(key.id)
            keys.append(key)
        groups[group] = sorted(keys, key=lambda key: (key.row, key.col))

    # Touching edges are normal. Ignore only the area caused by floating-point noise.
    for first, second in combinations((key for keys in groups.values() for key in keys), 2):
        if first.shape.intersects(second.shape) and first.shape.intersection(second.shape).area > 1e-10:
            raise ValueError(f"Keys overlap: {first.id} and {second.id}")
    return groups


def system_fonts():
    """Yield bold Japanese sans-serif faces in platform preference order."""
    if sys.platform == "win32":
        root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("meiryob.ttc", "YuGothB.ttc", "BIZ-UDGothicB.ttc"):
            yield root / name
    elif sys.platform == "darwin":
        root = Path("/System/Library/Fonts")
        for weight in ("W6", "W7", "W8", "W5"):
            yield root / f"ヒラギノ角ゴシック {weight}.ttc"

    roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
             Path.home() / ".local/share/fonts", Path.home() / "Library/Fonts"]
    if sys.platform == "win32" and "LOCALAPPDATA" in os.environ:
        roots.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    for root in roots:
        candidates = sorted(root.rglob("NotoSans*"))
        for stem in ("NotoSansCJKjp-Bold", "NotoSansCJK-Bold", "NotoSansJP-Bold",
                     "NotoSansJP-VariableFont_wght", "NotoSansJP[wght]"):
            yield from (path for path in candidates if path.stem == stem)


@dataclass(frozen=True)
class FontFace:
    path: Path
    weight: float | None

    @cache
    def at_size(self, size: int) -> ImageFont.FreeTypeFont:
        font = ImageFont.truetype(str(self.path), size, index=0)
        if self.weight is not None:
            font.set_variation_by_axes([self.weight])
        return font


def select_fonts(labels, font_path: Path | None) -> dict[str, FontFace]:
    # Check the character map so unsupported glyphs cannot become placeholder boxes.
    pending = dict.fromkeys(labels)
    selected = {}
    for path in (font_path,) if font_path is not None else system_fonts():
        if not path.is_file():
            continue
        try:
            with TTFont(path, fontNumber=0, lazy=True) as font:
                cmap = font.getBestCmap() or {}
                weight = None
                if "fvar" in font:
                    axes = font["fvar"].axes
                    if len(axes) == 1 and axes[0].axisTag == "wght":
                        weight = min(max(700, axes[0].minValue), axes[0].maxValue)
            face = FontFace(path, weight)
            face.at_size(16)
        except (OSError, ValueError, TTLibError):
            continue
        for label in list(pending):
            if all(ord(char) in cmap and cmap[ord(char)] != ".notdef" for char in label):
                selected[label] = face
                del pending[label]
        if not pending:
            return selected

    missing = ", ".join(repr(label) for label in list(pending)[:8])
    raise ValueError(f"No usable font for {missing}. Supply a Japanese font with --font PATH.")


def key_surfaces(key: Key) -> tuple[Polygon, Polygon]:
    # Inset each outline by half the shared gap, then round its corners in place.
    core = key.shape.buffer(-KEY_GAP / 2 - CORNER_RADIUS, join_style="mitre")
    outer = core.buffer(CORNER_RADIUS, join_style="round")
    inner = outer.buffer(-BORDER_WIDTH)
    if any(surface.geom_type != "Polygon" or surface.is_empty or surface.interiors
           for surface in (core, outer, inner)):
        raise ValueError(f"{key.id}: key shape is too narrow for the gap, rounding, or border settings")
    return outer, inner


def draw_label(image: Image.Image, key: Key, surface: Polygon, face: FontFace, pixel, scale: int) -> None:
    # Center text on the key's area. A deeply concave outline can have an external centroid.
    safe = surface.buffer(-LABEL_PADDING - 1 / scale)
    if safe.is_empty:
        raise ValueError(f"{key.id}: reduce LABEL_PADDING to fit text inside the key")
    center = surface.centroid
    if not safe.contains(center):
        center = safe.representative_point()

    # Fit the centered text rectangle inside the actual shape, including any notch.
    draw = ImageDraw.Draw(image)
    low, high = 1, round(MAX_FONT_SIZE * scale)
    fitted = None
    while low <= high:
        size = (low + high) // 2
        font = face.at_size(size)
        l, t, r, b = draw.textbbox((0, 0), key.label, font=font)
        dx, dy = (r - l) / (2 * scale), (b - t) / (2 * scale)
        if safe.covers(box(center.x - dx, center.y - dy, center.x + dx, center.y + dy)):
            fitted = (font, l, t, r, b)
            low = size + 1
        else:
            high = size - 1
    if fitted is None:
        raise ValueError(f"{key.id}: label does not fit inside the key")

    # Center the visible ink, independent of the font's bearings and line metrics.
    font, l, t, r, b = fitted
    mask = Image.new("L", (r - l, b - t))
    ImageDraw.Draw(mask).text((-l, -t), key.label, font=font, fill=255)
    ink = mask.getbbox()
    if ink is None:
        raise ValueError(f"{key.id}: label is too small to render")
    mask = mask.crop(ink)
    x, y = pixel((center.x, center.y))
    image.paste(TEXT_COLOR, (round(x - mask.width / 2), round(y - mask.height / 2)), mask)


def render_image(keys: list[Key], fonts: dict[str, FontFace]) -> Image.Image:
    left = min(key.shape.bounds[0] for key in keys)
    top = min(key.shape.bounds[1] for key in keys)
    right = max(key.shape.bounds[2] for key in keys)
    bottom = max(key.shape.bounds[3] for key in keys)
    width = math.ceil((right - left + 2 * MARGIN) * PIXELS_PER_UNIT)
    height = math.ceil((bottom - top + 2 * MARGIN) * PIXELS_PER_UNIT)
    scale = PIXELS_PER_UNIT * SUPERSAMPLE

    def pixel(point):
        x, y = point
        return ((x - left - (right - left) / 2) * scale + width * SUPERSAMPLE / 2,
                (y - top - (bottom - top) / 2) * scale + height * SUPERSAMPLE / 2)

    image = Image.new("RGB", (width * SUPERSAMPLE, height * SUPERSAMPLE), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for key in keys:
        outer, inner = key_surfaces(key)
        draw.polygon([pixel(point) for point in outer.exterior.coords], fill=BORDER_COLOR)
        draw.polygon([pixel(point) for point in inner.exterior.coords], fill=KEY_COLOR)
        draw_label(image, key, inner, fonts[key.label], pixel, scale)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="YAML layout path")
    parser.add_argument("--output", type=Path, default=Path("output"), help="PNG output directory")
    parser.add_argument("--font", type=Path, help="Japanese TTF, OTF, or TTC font (first face)")
    args = parser.parse_args()

    try:
        groups = load_layout(args.input)
        fonts = select_fonts((key.label for keys in groups.values() for key in keys), args.font)
        args.output.mkdir(parents=True, exist_ok=True)
        # Prepare both complete PNGs before replacing previous results.
        with TemporaryDirectory(dir=args.output) as staging:
            for name, group_names in OUTPUTS.items():
                keys = [key for group in group_names for key in groups[group]]
                with render_image(keys, fonts) as image:
                    image.save(Path(staging) / name, format="PNG")
            for name in OUTPUTS:
                (Path(staging) / name).replace(args.output / name)
    except (OSError, ValueError, OverflowError, GEOSException, yaml.YAMLError) as error:
        parser.exit(1, f"Error: {error}\n")

    counts = ", ".join(f"{name}: {len(keys)}" for name, keys in groups.items())
    print(f"Rendered {sum(map(len, groups.values()))} keys ({counts})")
    for name in OUTPUTS:
        print(args.output / name)


if __name__ == "__main__":
    main()
