"""Exercise the supplied layout, CLI, and rendered pixels."""

import contextlib
from copy import deepcopy
import io
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw
from shapely import box
import yaml

import render_keyboard as keyboard


LAYOUT = Path(__file__).resolve().parents[1] / keyboard.DEFAULT_INPUT
PITCH = keyboard.PIXELS_PER_UNIT


def pixel(image, x, y, left, span):
    # All supplied groups share the same 6.5u height and top origin.
    return (round((image.width - span * PITCH) / 2 + (x - left) * PITCH),
            round((image.height - 6.5 * PITCH) / 2 + y * PITCH))


def stroke_span(values, center):
    first = center
    last = center
    while first > 0 and values[first - 1] > 32:
        first -= 1
    while last < len(values) - 1 and values[last + 1] > 32:
        last += 1
    return last - first + 1


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.data = yaml.safe_load(LAYOUT.read_text(encoding="utf-8"))
        self.groups = self.data["groups"]
        self.main_keys = {key["id"]: key for key in self.groups["main"]}
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.input = Path(temporary.name) / "layout.yaml"

    def load(self):
        self.input.write_text(yaml.safe_dump(self.data, allow_unicode=True), encoding="utf-8")
        return keyboard.load_layout(self.input)

    def test_reference_counts_and_enter_outline(self):
        groups = keyboard.load_layout(LAYOUT)
        self.assertEqual({name: len(keys) for name, keys in groups.items()},
                         {"main": 79, "navigation": 13, "numpad": 17})
        keys = {key.id: key for key in groups["main"]}
        enter = keys["Enter"].shape
        bracket = keys["BracketRight"].shape
        self.assertAlmostEqual(enter.intersection(box(13.5, 2.5, 15, 3.5)).area, 1.5)
        self.assertAlmostEqual(enter.intersection(box(13.5, 3.5, 15, 4.5)).area, 1.25)
        self.assertEqual(bracket.bounds, (12.75, 3.5, 13.75, 4.5))
        self.assertGreater(box(*enter.bounds).intersection(bracket).area, 0)
        self.assertEqual(enter.intersection(bracket).area, 0)

    def test_rejects_invalid_fields_and_outlines(self):
        baseline = deepcopy(self.main_keys["Enter"])
        cases = [
            ("label", None, "label"),
            ("row", True, "row"),
            ("x", "13.5", "finite number"),
            ("y", float("nan"), "finite number"),
            ("w", 0, "positive"),
            ("h", -1, "positive"),
            ("outline", None, "outline"),
            ("outline", [[0, 0], [1.5], [0, 2]], "pair"),
            ("outline", [[0, 0], [1.5, 2], [0, 2], [1.5, 0]], "invalid"),
            ("outline", [[0, 0], [1, 0], [1, 2], [0, 2]], "must span"),
        ]
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                self.main_keys["Enter"].update(baseline, **{field: value})
                with self.assertRaisesRegex(ValueError, message):
                    self.load()
                self.main_keys["Enter"].update(baseline)
        del self.main_keys["Enter"]["x"]
        with self.assertRaisesRegex(ValueError, "missing: x"):
            self.load()

    def test_rejects_duplicate_ids_and_real_polygon_overlap(self):
        bracket = self.main_keys["BracketRight"]
        bracket["id"] = "NumpadEnter"
        with self.assertRaisesRegex(ValueError, "Duplicate key id"):
            self.load()
        bracket.update(id="BracketRight", w=1.01)
        with self.assertRaisesRegex(ValueError, "Keys overlap: Enter and BracketRight"):
            self.load()

    def test_rejects_bad_yaml(self):
        self.input.write_text("groups: [", encoding="utf-8")
        with self.assertRaises(yaml.YAMLError):
            keyboard.load_layout(self.input)

    def test_reads_edited_labels_and_coordinates_from_cli_input(self):
        self.main_keys["Esc"].update(label="変更", x=-0.25, row=8)
        groups = self.load()
        key = groups["main"][-1]
        self.assertEqual((key.id, key.label, key.shape.bounds), ("Esc", "変更", (-0.25, 0, 0.75, 1)))

        output = self.input.parent / "custom output"
        args = ["render_keyboard.py", "--input", str(self.input), "--output", str(output)]
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            keyboard.main()
        with Image.open(output / "main.png") as image:
            self.assertEqual(image.width, math.ceil((15.25 + 2 * keyboard.MARGIN) * PITCH))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        output = Path(temporary.name) / "output"
        cls.groups = keyboard.load_layout(LAYOUT)
        args = ["render_keyboard.py", "--input", str(LAYOUT), "--output", str(output)]
        with (patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()),
              patch.object(keyboard, "draw_label", wraps=keyboard.draw_label) as draw_label):
            keyboard.main()
            cls.drawn_labels = {call.args[1].id: call.args[1].label for call in draw_label.call_args_list}
        cls.images = {}
        for name in keyboard.OUTPUTS:
            with Image.open(output / name) as image:
                image.verify()
            with Image.open(output / name) as image:
                if image.format != "PNG" or image.mode != "RGB":
                    raise AssertionError(f"{name} must be an RGB PNG")
                cls.images[name] = image.convert("L")

    def test_exact_labels_are_passed_to_the_renderer(self):
        data = yaml.safe_load(LAYOUT.read_text(encoding="utf-8"))
        expected = {key["id"]: key["label"] for keys in data["groups"].values() for key in keys}
        self.assertEqual(self.drawn_labels, expected)
        for key, label in {"F13": "F13", "Menu": "Menu", "WinRight": "Win",
                           "Backspace": "＜", "ZenkakuHankaku": "半/全"}.items():
            self.assertEqual(self.drawn_labels[key], label)

    def test_wide_and_tall_keys_draw_at_their_yaml_sizes(self):
        image = self.images["main.png"]
        x, y = pixel(image, 6, 5.7, 0, 15)
        row = [image.getpixel((index, y)) for index in range(image.width)]
        self.assertAlmostEqual(stroke_span(row, x), (3.25 - keyboard.KEY_GAP) * PITCH, delta=2)

        image = self.images["navigation_numpad.png"]
        for y_u in (3.0, 5.0):
            x, y = pixel(image, 22.15, y_u, 15.5, 7.5)
            column = [image.getpixel((x, index)) for index in range(image.height)]
            self.assertAlmostEqual(stroke_span(column, y), (2 - keyboard.KEY_GAP) * PITCH, delta=2)

    def test_enter_step_and_no_internal_seam(self):
        image = self.images["main.png"]
        for y_u, width in ((2.85, 1.5), (4.15, 1.25)):
            x, y = pixel(image, 14.3, y_u, 0, 15)
            row = [image.getpixel((index, y)) for index in range(image.width)]
            self.assertAlmostEqual(stroke_span(row, x), (width - keyboard.KEY_GAP) * PITCH, delta=2)
        self.assertEqual(image.getpixel(pixel(image, 14.8, 3.5, 0, 15)), 66)
        self.assertEqual(image.getpixel(pixel(image, 13.75, 4, 0, 15)), 0)

    def test_every_key_is_separate_and_text_is_centered_within_its_shape(self):
        for name, left, span in (("main.png", 0, 15), ("navigation_numpad.png", 15.5, 7.5)):
            image = self.images[name]
            self.assertEqual(image.size, (math.ceil((span + 2 * keyboard.MARGIN) * PITCH),
                                          math.ceil((6.5 + 2 * keyboard.MARGIN) * PITCH)))
            caps = image.point(lambda value: 255 if value > 32 else 0)
            ink = image.point(lambda value: 255 if value > 160 else 0)
            allowed = Image.new("L", image.size)
            draw = ImageDraw.Draw(allowed)
            keys = [key for group in keyboard.OUTPUTS[name] for key in self.groups[group]]
            for key in keys:
                with self.subTest(image=name, key=key.id):
                    _, inner = keyboard.key_surfaces(key)
                    center = pixel(image, inner.centroid.x, inner.centroid.y, left, span)
                    self.assertEqual(caps.getpixel(center), 255)
                    ImageDraw.floodfill(caps, center, 128)

                    padded = inner.buffer(-keyboard.LABEL_PADDING + 3 / PITCH)
                    draw.polygon([pixel(image, x, y, left, span) for x, y in padded.exterior.coords], fill=255)
                    x0, y0, x1, y1 = key.shape.bounds
                    start_x, start_y = pixel(image, x0, y0, left, span)
                    end_x, end_y = pixel(image, x1, y1, left, span)
                    text_bounds = ink.crop((start_x, start_y, end_x, end_y)).getbbox()
                    self.assertIsNotNone(text_bounds, "missing visible label")
                    l, t, r, b = text_bounds
                    self.assertAlmostEqual(start_x + (l + r) / 2, center[0], delta=3)
                    self.assertAlmostEqual(start_y + (t + b) / 2, center[1], delta=3)
            self.assertIsNone(caps.point(lambda value: 255 if value == 255 else 0).getbbox())
            self.assertIsNone(ImageChops.subtract(ink, allowed).getbbox(), "label pixels outside key padding")
