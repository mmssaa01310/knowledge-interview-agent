#!/usr/bin/env python3
"""Render a 30-second, silent, vertical MUSUBI product promo."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "MUSUBI_PR_30sec_vertical.mp4"
POSTER = ROOT / "MUSUBI_PR_poster.png"
CAPTIONS = ROOT / "captions.srt"
VOICEOVER = ROOT / "voiceover-script.txt"

W, H, FPS = 1080, 1920, 24
JA_FONT_PATH = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
LATIN_REGULAR_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LATIN_BOLD_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

FOREST = "#183e2c"
DEEP = "#122f22"
LEAF = "#87a75a"
CITRUS = "#e8ce4d"
CREAM = "#f4f5e9"
PAPER = "#fffef8"
INK = "#1b3023"
TEXT = "#35473a"
MUTED = "#728071"
FAINT = "#9ca596"
BORDER = "#dce2d1"
SOFT_GREEN = "#e4ecd8"
SOFT_YELLOW = "#f5edbd"
WHITE = "#ffffff"


def font(size: int, latin: bool = False, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = (LATIN_BOLD_FONT_PATH if bold else LATIN_REGULAR_FONT_PATH) if latin else JA_FONT_PATH
    return ImageFont.truetype(path, size)


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def label(draw, xy, value, size, color, bold=False, stroke=0, anchor=None):
    segments = []
    for char in value:
        code = ord(char)
        is_japanese = (
            0x3000 <= code <= 0x303F
            or 0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF
        )
        use_latin = not is_japanese
        if segments and segments[-1][0] == use_latin:
            segments[-1] = (use_latin, segments[-1][1] + char)
        else:
            segments.append((use_latin, char))

    runs = [(text, font(size, latin=use_latin, bold=bold)) for use_latin, text in segments]
    if not runs:
        return
    width = sum(draw.textlength(text, font=f) for text, f in runs)
    ascent = max(f.getmetrics()[0] for _, f in runs)
    descent = max(f.getmetrics()[1] for _, f in runs)
    x, y = xy
    if anchor in ("mm", "lm", "rm"):
        if anchor == "mm":
            x -= width / 2
        elif anchor == "rm":
            x -= width
        y -= (ascent + descent) / 2
    baseline = y + ascent
    for text, f in runs:
        draw.text((x, baseline), text, font=f, fill=color, stroke_width=stroke, stroke_fill=color, anchor="ls")
        x += draw.textlength(text, font=f)


def text_lines(draw, x, y, lines, size, color, gap=1.2, stroke=0):
    line_height = int(size * gap)
    for index, line in enumerate(lines):
        label(draw, (x, y + line_height * index), line, size, color, stroke=stroke)
    return y + line_height * len(lines)


def cubic(p0, p1, p2, p3, count=36):
    points = []
    for index in range(count + 1):
        t = index / count
        u = 1 - t
        x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
        points.append((x, y))
    return points


def draw_knot(draw, x, y, size, color, width=6):
    scale = size / 64
    def p(px, py):
        return (x + px * scale, y + py * scale)
    top = cubic(p(8, 22), p(21, 22), p(20, 42), p(32, 42))
    top += cubic(p(32, 42), p(44, 42), p(42, 22), p(56, 22))[1:]
    bottom = cubic(p(8, 42), p(21, 42), p(20, 22), p(32, 22))
    bottom += cubic(p(32, 22), p(44, 22), p(42, 42), p(56, 42))[1:]
    draw.line(top, fill=color, width=max(2, round(width * scale)), joint="curve")
    draw.line(bottom, fill=color, width=max(2, round(width * scale)), joint="curve")


def draw_logo(draw, x, y, symbol_size=64, word_size=39, dark=False, word=True):
    tile = CITRUS
    ink = FOREST if not dark else DEEP
    rounded(draw, (x, y, x + symbol_size, y + symbol_size), 18, tile)
    draw_knot(draw, x + symbol_size * 0.08, y + symbol_size * 0.08, symbol_size * 0.84, ink, 4)
    if word:
        letter = font(word_size, latin=True)
        draw.text((x + symbol_size + 19, y + (symbol_size - word_size) // 2 - 1), "MUSUBI", font=letter, fill=WHITE if dark else FOREST, stroke_width=1, stroke_fill=WHITE if dark else FOREST)


def gradient(top: str, bottom: str) -> Image.Image:
    top_rgb = np.array(rgb(top), dtype=np.float32)
    bottom_rgb = np.array(rgb(bottom), dtype=np.float32)
    ramp = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    rows = (top_rgb[None, None, :] * (1 - ramp) + bottom_rgb[None, None, :] * ramp).astype(np.uint8)
    array = np.repeat(rows, W, axis=1)
    return Image.fromarray(array, "RGB")


def draw_ambient(image: Image.Image, dark=False, accent_y=320):
    haze = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(haze)
    if dark:
        draw.ellipse((560, accent_y - 230, 1320, accent_y + 530), fill=(232, 206, 77, 23))
        draw.ellipse((-390, 1140, 640, 2180), fill=(135, 167, 90, 18))
    else:
        draw.ellipse((605, accent_y - 240, 1285, accent_y + 440), fill=(232, 206, 77, 28))
        draw.ellipse((-400, 1200, 680, 2220), fill=(135, 167, 90, 20))
    haze = haze.filter(ImageFilter.GaussianBlur(115))
    image.alpha_composite(haze) if image.mode == "RGBA" else image.paste(Image.alpha_composite(image.convert("RGBA"), haze).convert("RGB"))


def base_scene(top=CREAM, bottom="#edf1e3", dark=False):
    image = gradient(top, bottom)
    draw = ImageDraw.Draw(image, "RGBA")
    # A quiet grid adds a crafted editorial texture without competing with text.
    dot = (255, 255, 255, 18) if dark else (24, 62, 44, 13)
    for x in range(26, W, 54):
        for y in range(27, H, 54):
            draw.ellipse((x, y, x + 2, y + 2), fill=dot)
    draw_ambient(image, dark=dark)
    return image


def shadow_panel(image: Image.Image, box, radius=32, fill=PAPER, outline=BORDER):
    x1, y1, x2, y2 = box
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((x1 + 3, y1 + 18, x2 + 3, y2 + 18), radius=radius, fill=(19, 49, 31, 34))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    image.alpha_composite(shadow) if image.mode == "RGBA" else image.paste(Image.alpha_composite(image.convert("RGBA"), shadow).convert("RGB"))
    draw = ImageDraw.Draw(image)
    rounded(draw, box, radius, fill, outline=outline, width=2)


def draw_top_brand(draw):
    draw_logo(draw, 66, 62, 58, 33)
    rounded(draw, (730, 71, 1011, 119), 23, PAPER, outline=BORDER, width=1)
    label(draw, (870, 95), "AIインタビュー / 知識整理", 19, MUTED, anchor="mm")


def draw_scene_heading(draw, tag, lines, subtitle):
    rounded(draw, (68, 168, 220, 216), 22, SOFT_YELLOW)
    label(draw, (144, 192), tag, 19, FOREST, anchor="mm")
    end_y = text_lines(draw, 68, 255, lines, 65, FOREST, gap=1.22, stroke=1)
    label(draw, (70, end_y + 12), subtitle, 28, MUTED)


def draw_caption(draw, primary, secondary=None, dark=False):
    color = CITRUS if dark else FOREST
    line = CITRUS if dark else LEAF
    draw.rounded_rectangle((70, 1643, 1008, 1649), radius=3, fill=line)
    label(draw, (72, 1690), primary, 34, color, stroke=1)
    if secondary:
        label(draw, (72, 1744), secondary, 24, "#d9e3d3" if dark else MUTED)


def draw_sidebar(image: Image.Image, active: str):
    x0, y0, w, h = 64, 555, 952, 944
    shadow_panel(image, (x0, y0, x0 + w, y0 + h), 30, PAPER, outline=BORDER)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((x0 + 2, y0 + 2, x0 + 214, y0 + h - 2), radius=28, fill=FOREST)
    draw.rectangle((x0 + 180, y0 + 2, x0 + 216, y0 + h - 2), fill=FOREST)
    draw_logo(draw, x0 + 24, y0 + 27, 43, 24, dark=True)
    label(draw, (x0 + 25, y0 + 105), "ワークスペース", 18, "#b8c9aa")
    nav = ["ホーム", "インタビュー", "ナレッジ", "記録", "ドキュメント"]
    nav_y = y0 + 155
    coords = {}
    for index, item in enumerate(nav):
        y = nav_y + index * 74
        coords[item] = (x0 + 22, y, x0 + 192, y + 54)
        if item == active:
            rounded(draw, coords[item], 15, CITRUS)
            color = FOREST
        else:
            color = "#e3eadb"
        # Small geometric nav marks stand in for the app's line icons.
        rounded(draw, (x0 + 39, y + 16, x0 + 57, y + 34), 5, color)
        label(draw, (x0 + 72, y + 27), item, 20, color, anchor="lm")
    label(draw, (x0 + 25, y0 + h - 62), "利用者メニュー", 17, "#b8c9aa")
    return x0 + 244, y0 + 32, w - 278, h - 64, coords


def draw_ui_header(draw, x, y, title, status=None):
    label(draw, (x, y), title, 30, INK, stroke=1)
    if status:
        rounded(draw, (x + 437, y - 4, x + 650, y + 40), 21, SOFT_GREEN)
        label(draw, (x + 543, y + 18), status, 18, FOREST, anchor="mm")


def draw_interview_ui(image: Image.Image):
    draw = ImageDraw.Draw(image)
    x, y, _, _, _ = draw_sidebar(image, "インタビュー")
    draw_ui_header(draw, x, y, "インタビュー", "対話中")
    label(draw, (x, y + 58), "段取り替えの判断基準", 23, MUTED)

    # Compact progress area.
    rounded(draw, (x, y + 112, x + 662, y + 218), 20, "#f6f8ef", outline=BORDER, width=1)
    label(draw, (x + 24, y + 136), "確認項目", 18, MUTED)
    labels = ["作業の流れ", "判断基準", "例外対応"]
    for i, text_value in enumerate(labels):
        px = x + 24 + i * 210
        active = i == 1
        rounded(draw, (px, y + 164, px + 188, y + 202), 18, CITRUS if active else WHITE, outline=None if active else BORDER, width=1)
        label(draw, (px + 94, y + 183), text_value, 17, FOREST if active else MUTED, anchor="mm")

    # AI prompt and a sample human answer in the conversation.
    rounded(draw, (x, y + 244, x + 574, y + 451), 22, WHITE, outline=BORDER, width=1)
    rounded(draw, (x + 20, y + 263, x + 221, y + 302), 18, SOFT_GREEN)
    label(draw, (x + 120, y + 282), "AIインタビューアー", 16, FOREST, anchor="mm")
    text_lines(draw, x + 24, y + 324, ["設備の調子が変わったとき、", "まずどこを確認しますか？"], 23, INK, gap=1.32)
    rounded(draw, (x + 115, y + 470, x + 662, y + 639), 22, "#f3f6e9", outline="#d8e2ce", width=1)
    label(draw, (x + 140, y + 493), "あなたの回答", 16, MUTED)
    text_lines(draw, x + 140, y + 531, ["まず普段と違う音や、", "製品の状態を見ています。"], 22, TEXT, gap=1.3)

    # Composer and voice affordance.
    rounded(draw, (x, y + 663, x + 662, y + 741), 21, WHITE, outline=BORDER, width=1)
    label(draw, (x + 24, y + 702), "回答を入力…", 20, FAINT, anchor="lm")
    rounded(draw, (x + 530, y + 676, x + 646, y + 728), 16, FOREST)
    label(draw, (x + 588, y + 702), "送信", 19, WHITE, anchor="mm")
    rounded(draw, (x + 6, y + 765, x + 238, y + 813), 23, SOFT_YELLOW)
    label(draw, (x + 122, y + 789), "音声インタビュー", 17, FOREST, anchor="mm")
    return (x + 332, y + 789)


def draw_knowledge_proposals(image: Image.Image):
    draw = ImageDraw.Draw(image)
    x, y, _, _, _ = draw_sidebar(image, "ナレッジ")
    draw_ui_header(draw, x, y, "ナレッジ候補", "AI提案・要確認")
    label(draw, (x, y + 61), "会話から整理した候補", 22, MUTED)

    # Main proposal card.
    rounded(draw, (x, y + 116, x + 662, y + 437), 24, WHITE, outline=BORDER, width=1)
    rounded(draw, (x + 23, y + 137, x + 190, y + 180), 19, SOFT_YELLOW)
    label(draw, (x + 107, y + 159), "判断の手がかり", 17, FOREST, anchor="mm")
    label(draw, (x + 23, y + 209), "作業の変化に気づいたら", 27, INK, stroke=1)
    text_lines(draw, x + 23, y + 255, ["普段と異なる音や状態がないかを", "確認する、という候補です。"], 21, TEXT, gap=1.38)
    rounded(draw, (x + 23, y + 353, x + 151, y + 391), 18, SOFT_GREEN)
    label(draw, (x + 87, y + 372), "要確認", 17, FOREST, anchor="mm")
    label(draw, (x + 178, y + 372), "会話の内容をもとに作成", 17, MUTED, anchor="lm")

    # More candidate types, with clear pending status.
    candidate_rows = [
        (y + 464, "適用条件", "どの作業・状況で使う知識か"),
        (y + 576, "補足情報", "例外や注意点をあとから追加"),
    ]
    for row_y, title, detail in candidate_rows:
        rounded(draw, (x, row_y, x + 662, row_y + 94), 19, "#f8f9f2", outline=BORDER, width=1)
        rounded(draw, (x + 18, row_y + 22, x + 161, row_y + 65), 17, SOFT_GREEN)
        label(draw, (x + 89, row_y + 43), title, 17, FOREST, anchor="mm")
        label(draw, (x + 181, row_y + 43), detail, 17, TEXT, anchor="lm")
    return (x + 560, y + 355)


def draw_review(image: Image.Image, approved=False):
    draw = ImageDraw.Draw(image)
    x, y, _, _, _ = draw_sidebar(image, "ナレッジ")
    draw_ui_header(draw, x, y, "提案の確認", "確認・承認")
    label(draw, (x, y + 61), "内容を確認して、必要なら編集できます", 20, MUTED)

    card = (x, y + 122, x + 662, y + 560)
    rounded(draw, card, 24, WHITE, outline=BORDER, width=1)
    badge_x = x + 24
    badge_color = SOFT_GREEN if approved else SOFT_YELLOW
    rounded(draw, (badge_x, y + 146, badge_x + 150, y + 190), 20, badge_color)
    label(draw, (badge_x + 75, y + 168), "承認済み" if approved else "要確認", 18, FOREST, anchor="mm")
    label(draw, (x + 24, y + 220), "作業前の判断基準", 28, INK, stroke=1)
    text_lines(draw, x + 24, y + 276, ["普段と違う音や状態に気づいたら、", "作業を進める前に状況を確かめる。"], 22, TEXT, gap=1.45)
    draw.line((x + 24, y + 372, x + 634, y + 372), fill=BORDER, width=2)
    label(draw, (x + 24, y + 405), "関連タグ", 17, MUTED)
    for i, tag in enumerate(("判断基準", "作業前")):
        tx = x + 121 + i * 132
        rounded(draw, (tx, y + 385, tx + 116, y + 426), 18, "#f0f3e8")
        label(draw, (tx + 58, y + 406), tag, 16, FOREST, anchor="mm")
    label(draw, (x + 24, y + 474), "提案を確認するのは、チームの利用者です。", 17, MUTED)

    button_y = y + 598
    rounded(draw, (x + 219, button_y, x + 418, button_y + 61), 18, WHITE, outline=BORDER, width=2)
    label(draw, (x + 318, button_y + 31), "内容を修正", 19, TEXT, anchor="mm")
    button_box = (x + 434, button_y, x + 662, button_y + 61)
    rounded(draw, button_box, 18, FOREST if approved else CITRUS)
    label(draw, (button_box[0] + 114, button_y + 31), "承認済み" if approved else "承認する", 19, WHITE if approved else FOREST, anchor="mm")
    if approved:
        cx, cy = button_box[0] + 30, button_y + 31
        draw.line((cx - 9, cy, cx - 1, cy + 8, cx + 13, cy - 10), fill=WHITE, width=4, joint="curve")
    return button_box


def draw_knowledge_list(image: Image.Image):
    draw = ImageDraw.Draw(image)
    x, y, _, _, _ = draw_sidebar(image, "ナレッジ")
    draw_ui_header(draw, x, y, "ナレッジ一覧", "確認済み")
    rounded(draw, (x, y + 66, x + 662, y + 126), 18, WHITE, outline=BORDER, width=1)
    label(draw, (x + 25, y + 96), "知識や記録を検索", 19, FAINT, anchor="lm")
    rounded(draw, (x + 602, y + 79, x + 645, y + 113), 12, SOFT_GREEN)
    draw.ellipse((x + 612, y + 86, x + 628, y + 102), outline=FOREST, width=3)
    draw.line((x + 625, y + 100, x + 634, y + 109), fill=FOREST, width=3)

    tags = ["工程", "判断基準", "安全"]
    for i, tag in enumerate(tags):
        tx = x + i * 135
        rounded(draw, (tx, y + 145, tx + 117, y + 190), 20, SOFT_GREEN if i == 1 else "#f8f9f2", outline=None if i == 1 else BORDER)
        label(draw, (tx + 58, y + 168), tag, 17, FOREST if i == 1 else MUTED, anchor="mm")

    rows = [
        ("作業前のチェックポイント", "承認済み  ·  工程 / 判断基準", "K"),
        ("異常時の確認と報告", "承認済み  ·  安全 / 設備", "K"),
        ("作業手順書.pdf", "ドキュメント  ·  取り込み完了", "D"),
    ]
    for index, (title, detail, mark) in enumerate(rows):
        row_y = y + 220 + index * 167
        rounded(draw, (x, row_y, x + 662, row_y + 145), 21, WHITE, outline=BORDER, width=1)
        rounded(draw, (x + 20, row_y + 26, x + 81, row_y + 87), 17, SOFT_YELLOW if mark == "D" else SOFT_GREEN)
        label(draw, (x + 50, row_y + 57), mark, 21, FOREST, anchor="mm", bold=True)
        label(draw, (x + 103, row_y + 40), title, 22, INK, stroke=1)
        label(draw, (x + 103, row_y + 91), detail, 16, MUTED)
        rounded(draw, (x + 562, row_y + 52, x + 634, row_y + 91), 18, SOFT_GREEN if mark == "K" else SOFT_YELLOW)
        label(draw, (x + 598, row_y + 71), "知識" if mark == "K" else "文書", 15, FOREST, anchor="mm")


def make_intro() -> Image.Image:
    image = base_scene(DEEP, FOREST, dark=True)
    draw = ImageDraw.Draw(image)
    # Large, low-contrast looping lines hint at a connection being made.
    for offset, alpha in ((0, 12), (30, 7), (60, 4)):
        paths = [
            cubic((40, 760 + offset), (330, 950 + offset), (285, 1150 + offset), (540, 1180 + offset)),
            cubic((540, 1180 + offset), (795, 1210 + offset), (730, 920 + offset), (1040, 790 + offset)),
            cubic((40, 1160 - offset), (330, 980 - offset), (285, 790 - offset), (540, 775 - offset)),
            cubic((540, 775 - offset), (795, 750 - offset), (730, 1040 - offset), (1040, 1170 - offset)),
        ]
        color = (232, 206, 77, alpha)
        for path in paths:
            draw.line(path, fill=color, width=3)
    draw_logo(draw, 371, 397, 80, 48, dark=True)
    draw_knot(draw, 452, 680, 176, CITRUS, 5)
    text_lines(draw, 117, 1032, ["現場の知恵を、", "みんなの知識へ。"], 75, WHITE, gap=1.28, stroke=1)
    label(draw, (121, 1260), "AIインタビューから、確認できるナレッジへ。", 27, "#d9e3d3")
    rounded(draw, (121, 1350, 539, 1409), 28, CITRUS)
    label(draw, (330, 1380), "AIインタビュー × 知識整理", 21, FOREST, anchor="mm")
    return image


def make_light_scene(tag, heading, subtitle, caption, ui_renderer):
    image = base_scene()
    draw = ImageDraw.Draw(image)
    draw_top_brand(draw)
    draw_scene_heading(draw, tag, heading, subtitle)
    ui_renderer(image)
    label(draw, (834, 1522), "画面イメージ", 16, MUTED)
    draw_caption(draw, caption)
    return image


def make_approval_scenes():
    pending = base_scene(DEEP, FOREST, dark=True)
    draw = ImageDraw.Draw(pending)
    draw_logo(draw, 66, 62, 58, 33, dark=True)
    rounded(draw, (786, 71, 1011, 119), 23, "#254c37", outline="#46674e", width=1)
    label(draw, (898, 95), "人が確認", 19, "#e1ead6", anchor="mm")
    rounded(draw, (68, 168, 220, 216), 22, "#314e34")
    label(draw, (144, 192), "確かめる", 19, CITRUS, anchor="mm")
    text_lines(draw, 68, 255, ["AIの提案は、", "人が確かめてから。"], 65, WHITE, gap=1.22, stroke=1)
    label(draw, (70, 430), "修正や承認を経て、チームで使える知識に。", 27, "#cbd9c7")
    draw_review(pending, approved=False)
    label(ImageDraw.Draw(pending), (834, 1522), "画面イメージ", 16, "#aabca7")
    draw_caption(ImageDraw.Draw(pending), "提案を確かめ、必要なら修正。", "承認した内容を、チームの知識へ。", dark=True)

    approved = pending.copy()
    draw_review(approved, approved=True)
    label(ImageDraw.Draw(approved), (834, 1522), "画面イメージ", 16, "#aabca7")
    return pending, approved


def make_outro() -> Image.Image:
    image = base_scene(DEEP, FOREST, dark=True)
    draw = ImageDraw.Draw(image)
    # Open loops converge behind the closing brand mark.
    for i, alpha in enumerate((20, 34, 48)):
        yy = 810 + i * 13
        draw.line(cubic((76, yy), (385, yy + 110), (387, yy + 288), (540, yy + 330)), fill=(232, 206, 77, alpha), width=4)
        draw.line(cubic((1004, yy), (695, yy + 110), (693, yy + 288), (540, yy + 330)), fill=(135, 167, 90, alpha), width=4)
        draw.line(cubic((76, yy + 330), (385, yy + 218), (387, yy + 42), (540, yy)), fill=(135, 167, 90, alpha), width=4)
        draw.line(cubic((1004, yy + 330), (695, yy + 218), (693, yy + 42), (540, yy)), fill=(232, 206, 77, alpha), width=4)
    draw_logo(draw, 371, 394, 80, 48, dark=True)
    draw_knot(draw, 450, 741, 180, CITRUS, 5)
    text_lines(draw, 145, 1070, ["知恵をつなぎ、", "次の一歩へ。"], 74, WHITE, gap=1.28, stroke=1)
    label(draw, (151, 1290), "AIインタビュー / ナレッジ構造化アプリ", 25, "#d9e3d3")
    rounded(draw, (151, 1381, 929, 1461), 30, CITRUS)
    label(draw, (540, 1421), "MUSUBI   ·   現場の知恵を、みんなの知識へ。", 21, FOREST, anchor="mm")
    return image


def image_to_bgr(image: Image.Image):
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def zoom_frame(frame: np.ndarray, amount: float):
    if amount < 0.0001:
        return frame
    sw = int(W * (1 + amount))
    sh = int(H * (1 + amount))
    scaled = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_LINEAR)
    x0 = (sw - W) // 2
    y0 = (sh - H) // 2
    return scaled[y0 : y0 + H, x0 : x0 + W]


def draw_motion(frame: np.ndarray, scene_index: int, local_time: float):
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    if scene_index in (0, 5):
        cx, cy = (540, 766) if scene_index == 0 else (540, 830)
        radius = 150
        for i in range(3):
            angle = local_time * 1.35 + i * 2 * math.pi / 3
            px = cx + math.cos(angle) * radius
            py = cy + math.sin(angle) * radius * 0.52
            pulse = 7 + 2 * math.sin(local_time * 3 + i)
            draw.ellipse((px - pulse, py - pulse, px + pulse, py + pulse), fill=(232, 206, 77, 230))
    elif scene_index == 1:
        # A small moving waveform in the audio affordance indicates that voice is available.
        base_x, base_y = 705, 1344
        for i in range(22):
            amplitude = 5 + 15 * abs(math.sin(local_time * 5.3 + i * 0.63))
            x = base_x + i * 13
            draw.rounded_rectangle((x, base_y - amplitude, x + 5, base_y + amplitude), radius=3, fill=(135, 167, 90, 230))
    elif scene_index == 2:
        # A gold signal travels down the pending suggestion stack.
        phase = (local_time * 0.32) % 1.0
        x = 304 + int(630 * phase)
        y = 1010 + int(280 * phase)
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(232, 206, 77, 235))
    elif scene_index == 3:
        pulse = 0.22 + 0.16 * (0.5 + 0.5 * math.sin(local_time * 4))
        x1, y1, x2, y2 = 735, 1182, 977, 1249
        draw.rounded_rectangle((x1 - 5, y1 - 5, x2 + 5, y2 + 5), radius=22, outline=(232, 206, 77, int(255 * pulse)), width=4)
    elif scene_index == 4:
        phase = (local_time * 0.2) % 1.0
        x = 324 + int(560 * phase)
        y = 964 + int(330 * phase)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(232, 206, 77, 230))
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def srt_timestamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_supporting_files():
    boundaries = [0.0, 4.4, 9.5, 14.6, 19.7, 24.8, 30.0]
    captions = [
        "現場の知恵を、みんなの知識へ。\nAIインタビューから、確認できるナレッジへ。",
        "経験の“なぜ”まで、対話で聞く。\nテキスト・音声のインタビューに対応。",
        "会話から、ナレッジ候補を整理。\nAIの提案は、確認前の候補として表示。",
        "AIの提案は、人が確かめてから。\n修正や承認を経て、チームの知識へ。",
        "確認した知識を、チームで活用。\n記録やドキュメントも整理。",
        "知恵をつなぎ、次の一歩へ。\nMUSUBI — AIインタビュー / ナレッジ構造化",
    ]
    entries = []
    for i, caption in enumerate(captions):
        entries.append(f"{i + 1}\n{srt_timestamp(boundaries[i])} --> {srt_timestamp(boundaries[i + 1])}\n{caption}\n")
    CAPTIONS.write_text("\n".join(entries), encoding="utf-8")
    VOICEOVER.write_text(
        "MUSUBI PR動画 ナレーション案（約30秒）\n\n"
        "現場で培った経験や、判断のコツ。\n"
        "MUSUBIは、AIインタビューで一人ひとりの知恵を聞き取ります。\n"
        "会話から整理された知識候補は、人が確認し、必要に応じて修正・承認。\n"
        "記録やドキュメントとあわせて、チームの知識として活用できます。\n"
        "現場の知恵を、みんなの知識へ。MUSUBI。\n\n"
        "※動画は字幕付き・音声なしです。ナレーション案は後付け用です。\n",
        encoding="utf-8",
    )


def render():
    intro = make_intro()
    interview = make_light_scene(
        "聞く", ["経験の“なぜ”まで、", "対話で聞く。"],
        "判断の背景を、テキスト・音声で引き出す。",
        "経験者の声から、判断の背景まで。",
        draw_interview_ui,
    )
    proposals = make_light_scene(
        "整理する", ["会話から、", "知識候補を整理。"],
        "回答を、確認しやすい構造へ。",
        "会話から、確認しやすい知識候補へ。",
        draw_knowledge_proposals,
    )
    pending, approved = make_approval_scenes()
    knowledge = make_light_scene(
        "活用する", ["確認した知識を、", "チームで活用。"],
        "タグやドキュメントとあわせて整理。",
        "記録や文書も整理し、知識を共有。",
        draw_knowledge_list,
    )
    outro = make_outro()

    scenes = [intro, interview, proposals, pending, knowledge, outro]
    durations = [4.4, 5.1, 5.1, 5.1, 5.1, 5.2]
    starts = np.cumsum([0.0] + durations[:-1]).tolist()
    scene_arrays = [image_to_bgr(image) for image in scenes]
    approved_array = image_to_bgr(approved)
    pending_array = image_to_bgr(pending)
    starts_frame = [round(t * FPS) for t in starts]
    frame_count = round(sum(durations) * FPS)

    writer = cv2.VideoWriter(str(OUTPUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open an MP4 video writer")

    scene_index = 0
    transition_frames = round(0.36 * FPS)
    for frame_number in range(frame_count):
        time_sec = frame_number / FPS
        while scene_index < len(durations) - 1 and time_sec >= starts[scene_index] + durations[scene_index]:
            scene_index += 1
        local_time = time_sec - starts[scene_index]
        progress = min(1.0, max(0.0, local_time / durations[scene_index]))
        current = zoom_frame(scene_arrays[scene_index], 0.012 * progress)

        if scene_index == 3:
            approve_start = 2.3
            approval_alpha = min(1.0, max(0.0, (local_time - approve_start) / 0.45))
            approval_alpha = approval_alpha * approval_alpha * (3 - 2 * approval_alpha)
            current = cv2.addWeighted(current, 1 - approval_alpha, approved_array, approval_alpha, 0)

        scene_end = starts[scene_index] + durations[scene_index]
        remaining = scene_end - time_sec
        if scene_index < len(scenes) - 1 and remaining <= transition_frames / FPS:
            next_index = scene_index + 1
            alpha = 1 - remaining / (transition_frames / FPS)
            alpha = min(1.0, max(0.0, alpha))
            alpha = alpha * alpha * (3 - 2 * alpha)
            next_frame = scene_arrays[next_index]
            if next_index == 3:
                next_frame = pending_array
            current = cv2.addWeighted(current, 1 - alpha, next_frame, alpha, 0)

        current = draw_motion(current, scene_index, local_time)
        writer.write(current)
        if frame_number % (FPS * 5) == 0:
            print(f"Rendered {time_sec:05.1f}s / 30.0s", flush=True)

    writer.release()
    make_outro().save(POSTER, quality=95)
    write_supporting_files()
    print(f"Video: {OUTPUT}")
    print(f"Poster: {POSTER}")
    print(f"Captions: {CAPTIONS}")
    print(f"Voice-over script: {VOICEOVER}")


if __name__ == "__main__":
    render()
