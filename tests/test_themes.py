"""Темы оформления: токены, контраст, безопасный выбор и наличие картинок у каждой темы."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="ares-themes-appdata-"))
sys.path.insert(0, str(ROOT / "app"))

from themes import (THEMES, THEME_ORDER, ATMOSPHERE_LEVELS, DEFAULT_THEME, asset_tokens, contrast,  # noqa: E402
                    full_palette, get_atmosphere, get_theme_id)

ASSETS = ROOT / "app" / "assets"
REQUIRED_TOKENS = ("background", "sidebar", "surface", "surface_high", "field", "text", "text_soft", "muted",
                   "muted_dim", "accent", "accent_hover", "accent_deep", "on_accent", "selection", "border",
                   "border_soft", "hover_row", "focus", "green", "amber", "red", "blue", "cyan", "menu_bg",
                   "dialog_bg", "separator", "palette_surface", "hero_backdrop", "shade")
REQUIRED_FILES = ("panel.png", "accent_frame_0.png", "button_frame_11.png", "field_anim_11.png", "switch_9.png",
                  "tab_selected.png", "heading.png", "nav_active.png", "hero_left.png", "hero_right.png",
                  "menu.png", "dialog.png", "toast.png", "particles.json", "icons.dat", "icons.json")


def theme_dir(theme_id):
    return ASSETS if theme_id == "aurora" else ASSETS / "themes" / theme_id


class ThemeTokenTests(unittest.TestCase):
    def test_five_new_themes_plus_aurora(self):
        self.assertEqual(set(THEME_ORDER), set(THEMES))
        self.assertEqual(set(THEMES) - {"aurora"}, {"anime", "winter", "spring", "summer", "autumn"})
        for theme in THEMES.values():
            self.assertTrue(theme["name"] and theme["description"])

    def test_every_theme_has_all_tokens(self):
        for theme_id in THEMES:
            palette = full_palette(theme_id)
            for token in REQUIRED_TOKENS:
                self.assertRegex(palette.get(token, ""), r"^#[0-9A-Fa-f]{6}$", f"{theme_id}.{token}")

    def test_text_contrast(self):
        """WCAG: основной и вторичный текст ≥ 4.5 на поверхностях, текст на акценте ≥ 4.5 на кнопке."""
        for theme_id in THEMES:
            p = full_palette(theme_id)
            for fg, bg in (("text", "surface"), ("text", "background"), ("text_soft", "surface"),
                           ("muted", "surface"), ("muted", "background"), ("text", "field"), ("text", "selection"),
                           ("accent", "surface"), ("red", "surface"), ("green", "surface"), ("amber", "surface"),
                           ("muted", "sidebar"), ("text", "hero_backdrop")):
                self.assertGreaterEqual(contrast(p[fg], p[bg]), 4.5, f"{theme_id}: {fg} on {bg}")
            if theme_id != "aurora":  # фирменная «Аврора» сохранена без изменений
                a = asset_tokens(theme_id)
                for fill in (a["acc_top"], a["acc_bottom"], a["acc_top_hover"], a["acc_bottom_hover"], a["acc_pressed_top"]):
                    self.assertGreaterEqual(contrast(p["on_accent"], fill), 4.5, f"{theme_id}: on_accent on {fill}")

    def test_aurora_unchanged(self):
        p = full_palette("aurora")
        self.assertEqual((p["background"], p["surface"], p["accent"], p["text"]), ("#0B0B12", "#13131C", "#A898FF", "#F3F2FA"))

    def test_unknown_or_corrupted_settings_fall_back(self):
        for value in (None, "", "космос", 42, ["winter"], {"id": "winter"}):
            self.assertEqual(get_theme_id(value), DEFAULT_THEME)
            self.assertIn(get_atmosphere(value), ATMOSPHERE_LEVELS)
        self.assertEqual(get_theme_id("winter"), "winter")
        self.assertEqual(get_atmosphere("off"), "off")


class ThemeAssetTests(unittest.TestCase):
    def test_assets_exist_for_every_theme(self):
        for theme_id in THEMES:
            folder = theme_dir(theme_id)
            for name in REQUIRED_FILES:
                self.assertTrue((folder / name).exists(), f"{theme_id}: {name}")
            self.assertTrue((ASSETS / f"theme_preview_{theme_id}.png").exists())

    def test_icon_bundle_covers_palette_colours(self):
        for theme_id in THEMES:
            index = json.loads((theme_dir(theme_id) / "icons.json").read_text(encoding="utf-8"))
            p = full_palette(theme_id)
            for token in ("accent", "text", "text_soft", "muted", "muted_dim", "on_accent", "red", "green", "amber"):
                name = f"icon_lock_17_{p[token][1:].lower()}.png"
                self.assertIn(name, index, f"{theme_id}: {token}")

    def test_particles_manifest(self):
        kinds = {"aurora": "sparks", "anime": "stars", "winter": "snow", "spring": "petals", "summer": "dust",
                 "autumn": "leaves"}
        for theme_id, kind in kinds.items():
            manifest = json.loads((theme_dir(theme_id) / "particles.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], kind)
            self.assertTrue(manifest["sprites"])
            for frames in manifest["sprites"]:
                for name in frames:
                    self.assertTrue((theme_dir(theme_id) / name).exists(), f"{theme_id}: {name}")


class ColorMapTests(unittest.TestCase):
    def test_core_tokens_map_exactly(self):
        from theme_ui import color_map
        for a in THEMES:
            for b in THEMES:
                old, new = full_palette(a), full_palette(b)
                mapping = color_map(old, new)
                for token in ("background", "surface", "text", "text_soft", "muted", "accent", "field", "sidebar"):
                    self.assertEqual(mapping[old[token].lower()].lower(), new[token].lower(), f"{a}->{b}: {token}")


if __name__ == "__main__":
    unittest.main()
