#!/usr/bin/env python3
"""Render a 30-second, silent, landscape MUSUBI promo for website embeds."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from render_musubi_pr import (
    BORDER,
    CITRUS,
    CREAM,
    DEEP,
    FOREST,
    INK,
    LEAF,
    MUTED,
    PAPER,
    SOFT_GREEN,
    SOFT_YELLOW,
    TEXT,
    WHITE,
    cubic,
    draw_knot,
    draw_logo,
    font,
    label,
    rounded,
)


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "MUSUBI_PR_30sec_landscape.mp4"
POSTER = ROOT / "MUSUBI_PR_poster_landscape.png"
CAPTIONS = ROOT / "captions-landscape.srt"
EMBED = ROOT / "MUSUBI_PR_embed_example.html"

W, H, FPS = 1920, 1080, 24
X0, Y0, SW, SH, SIDEBAR = 830, 184, 1010, 724, 218


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def gradient(top: str, bottom: str) -> Image.Image:
    top_rgb = np.array(rgb(top), dtype=np.float32)
    bottom_rgb = np.array(rgb(bottom), dtype=np.float32)
    ramp = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    rows = (top_rgb[None, None, :] * (1 - ramp) + bottom_rgb[None, None, :] * ramp).astype(np.uint8)
    return Image.fromarray(np.repeat(rows, W, axis=1), "RGB")


def add_ambient(image: Image.Image, dark=False):
    haze = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(haze)
    if dark:
        draw.ellipse((1090, -210, 2080, 800), fill=(232, 206, 77, 25))
        draw.ellipse((-280, 580, 690, 1400), fill=(135, 167, 90, 18))
    else:
        draw.ellipse((1170, -190, 2080, 710), fill=(232, 206, 77, 32))
        draw.ellipse((-320, 620, 660, 1390), fill=(135, 167, 90, 20))
    haze = haze.filter(ImageFilter.GaussianBlur(120))
    image.paste(Image.alpha_composite(image.convert("RGBA"), haze).convert("RGB"))


def base_scene(dark=False):
    image = gradient(DEEP, FOREST) if dark else gradient(CREAM, "#edf1e3")
    draw = ImageDraw.Draw(image, "RGBA")
    dot = (255, 255, 255, 18) if dark else (24, 62, 44, 12)
    for x in range(28, W, 56):
        for y in range(24, H, 52):
            draw.ellipse((x, y, x + 2, y + 2), fill=dot)
    add_ambient(image, dark)
    return image


def shadow_panel(image, box, radius=28, fill=PAPER, outline=BORDER):
    x1, y1, x2, y2 = box
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x1 + 4, y1 + 16, x2 + 4, y2 + 16), radius=radius, fill=(19, 49, 31, 38))
    shadow = shadow.filter(ImageFilter.GaussianBlur(23))
    image.paste(Image.alpha_composite(image.convert("RGBA"), shadow).convert("RGB"))
    rounded(ImageDraw.Draw(image), box, radius, fill, outline=outline, width=2)


def draw_brand(draw, dark=False):
    draw_logo(draw, 112, 58, 60, 34, dark=dark)
    color = "#254c37" if dark else PAPER
    border = "#46674e" if dark else BORDER
    rounded(draw, (1450, 68, 1810, 119), 24, color, outline=border, width=1)
    label(draw, (1630, 94), "AIインタビュー / 知識整理", 20, "#e3eadb" if dark else MUTED, anchor="mm")


def draw_window(image: Image.Image, active: str):
    shadow_panel(image, (X0, Y0, X0 + SW, Y0 + SH), 28, PAPER, outline=BORDER)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((X0 + 2, Y0 + 2, X0 + SIDEBAR, Y0 + SH - 2), radius=27, fill=FOREST)
    draw.rectangle((X0 + SIDEBAR - 34, Y0 + 2, X0 + SIDEBAR + 2, Y0 + SH - 2), fill=FOREST)
    draw_logo(draw, X0 + 22, Y0 + 19, 43, 23, dark=True)
    label(draw, (X0 + 23, Y0 + 86), "ワークスペース", 16, "#b8c9aa")
    nav = ["ホーム", "インタビュー", "ナレッジ", "記録", "ドキュメント"]
    for index, item in enumerate(nav):
        y = Y0 + 119 + index * 73
        if item == active:
            rounded(draw, (X0 + 18, y, X0 + SIDEBAR - 20, y + 53), 15, CITRUS)
            ink = FOREST
        else:
            ink = "#e3eadb"
        rounded(draw, (X0 + 34, y + 17, X0 + 51, y + 34), 5, ink)
        label(draw, (X0 + 65, y + 26), item, 19, ink, anchor="lm")
    label(draw, (X0 + 23, Y0 + SH - 42), "利用者メニュー", 16, "#b8c9aa")
    return X0 + SIDEBAR + 30, Y0 + 27, SW - SIDEBAR - 54


def draw_ui_title(draw, x, y, width, title, status):
    label(draw, (x, y), title, 28, INK, stroke=1)
    rounded(draw, (x + width - 207, y - 4, x + width, y + 39), 20, SOFT_GREEN)
    label(draw, (x + width - 103, y + 17), status, 17, FOREST, anchor="mm")


def draw_interview(image):
    draw = ImageDraw.Draw(image)
    x, y, width = draw_window(image, "インタビュー")
    draw_ui_title(draw, x, y, width, "インタビュー", "対話中")
    label(draw, (x, y + 45), "段取り替えの判断基準", 20, MUTED)

    rounded(draw, (x, y + 83, x + width, y + 173), 18, "#f6f8ef", outline=BORDER, width=1)
    label(draw, (x + 19, y + 103), "確認項目", 16, MUTED)
    for index, item in enumerate(("作業の流れ", "判断基準", "例外対応")):
        px = x + 128 + index * 196
        selected = index == 1
        rounded(draw, (px, y + 98, px + 174, y + 143), 18, CITRUS if selected else WHITE, outline=None if selected else BORDER)
        label(draw, (px + 87, y + 120), item, 17, FOREST if selected else MUTED, anchor="mm")

    rounded(draw, (x, y + 194, x + width, y + 350), 19, WHITE, outline=BORDER, width=1)
    rounded(draw, (x + 20, y + 210, x + 216, y + 250), 18, SOFT_GREEN)
    label(draw, (x + 118, y + 230), "AIインタビューアー", 16, FOREST, anchor="mm")
    label(draw, (x + 22, y + 268), "設備の調子が変わったとき、まずどこを確認しますか？", 22, INK)

    rounded(draw, (x + 143, y + 367, x + width, y + 490), 19, "#f3f6e9", outline="#d8e2ce", width=1)
    label(draw, (x + 164, y + 390), "あなたの回答", 15, MUTED)
    text = "まず普段と違う音や、製品の状態を見ています。"
    label(draw, (x + 164, y + 430), text, 21, TEXT)

    rounded(draw, (x, y + 510, x + width, y + 572), 18, WHITE, outline=BORDER, width=1)
    label(draw, (x + 20, y + 541), "回答を入力…", 18, "#9ca596", anchor="lm")
    rounded(draw, (x + width - 117, y + 520, x + width - 11, y + 562), 14, FOREST)
    label(draw, (x + width - 64, y + 541), "送信", 17, WHITE, anchor="mm")
    rounded(draw, (x, y + 589, x + 235, y + 635), 21, SOFT_YELLOW)
    label(draw, (x + 117, y + 612), "音声インタビュー", 17, FOREST, anchor="mm")


def draw_proposals(image):
    draw = ImageDraw.Draw(image)
    x, y, width = draw_window(image, "ナレッジ")
    draw_ui_title(draw, x, y, width, "ナレッジ候補", "AI提案・要確認")
    label(draw, (x, y + 48), "会話から整理した候補", 20, MUTED)

    rounded(draw, (x, y + 91, x + width, y + 375), 21, WHITE, outline=BORDER, width=1)
    rounded(draw, (x + 23, y + 111, x + 187, y + 153), 18, SOFT_YELLOW)
    label(draw, (x + 105, y + 132), "判断の手がかり", 17, FOREST, anchor="mm")
    label(draw, (x + 23, y + 178), "作業の変化に気づいたら", 25, INK, stroke=1)
    text_lines = ["普段と異なる音や状態がないかを確認する、", "という候補です。"]
    label(draw, (x + 23, y + 220), text_lines[0], 20, TEXT)
    label(draw, (x + 23, y + 250), text_lines[1], 20, TEXT)
    rounded(draw, (x + 23, y + 314, x + 149, y + 354), 18, SOFT_GREEN)
    label(draw, (x + 86, y + 334), "要確認", 16, FOREST, anchor="mm")
    label(draw, (x + 170, y + 334), "会話の内容をもとに作成", 16, MUTED)

    for index, (title, detail) in enumerate((("適用条件", "どの作業・状況で使う知識か"), ("補足情報", "例外や注意点をあとから追加"))):
        cx = x + index * 360
        rounded(draw, (cx, y + 395, cx + 340, y + 520), 18, "#f8f9f2", outline=BORDER, width=1)
        rounded(draw, (cx + 15, y + 412, cx + 141, y + 451), 17, SOFT_GREEN)
        label(draw, (cx + 78, y + 431), title, 16, FOREST, anchor="mm")
        label(draw, (cx + 16, y + 473), detail, 16, TEXT)


def draw_review(image, approved=False):
    draw = ImageDraw.Draw(image)
    x, y, width = draw_window(image, "ナレッジ")
    draw_ui_title(draw, x, y, width, "提案の確認", "確認・承認")
    label(draw, (x, y + 48), "内容を確認して、必要なら編集できます", 19, MUTED)

    rounded(draw, (x, y + 91, x + width, y + 440), 21, WHITE, outline=BORDER, width=1)
    badge = SOFT_GREEN if approved else SOFT_YELLOW
    rounded(draw, (x + 22, y + 110, x + 174, y + 151), 18, badge)
    label(draw, (x + 98, y + 131), "承認済み" if approved else "要確認", 17, FOREST, anchor="mm")
    label(draw, (x + 23, y + 179), "作業前の判断基準", 26, INK, stroke=1)
    label(draw, (x + 23, y + 224), "普段と違う音や状態に気づいたら、作業を進める前に状況を確かめる。", 20, TEXT)
    draw.line((x + 23, y + 282, x + width - 22, y + 282), fill=BORDER, width=2)
    label(draw, (x + 23, y + 311), "関連タグ", 16, MUTED)
    for i, tag in enumerate(("判断基準", "作業前")):
        tx = x + 108 + i * 130
        rounded(draw, (tx, y + 292, tx + 114, y + 333), 17, "#f0f3e8")
        label(draw, (tx + 57, y + 312), tag, 16, FOREST, anchor="mm")
    label(draw, (x + 23, y + 383), "AI提案は、利用者が確認してから知識にします。", 17, MUTED)

    button_y = y + 469
    rounded(draw, (x + width - 347, button_y, x + width - 173, button_y + 55), 16, WHITE, outline=BORDER, width=2)
    label(draw, (x + width - 260, button_y + 28), "内容を修正", 17, TEXT, anchor="mm")
    approve_box = (x + width - 157, button_y, x + width, button_y + 55)
    rounded(draw, approve_box, 16, FOREST if approved else CITRUS)
    label(draw, (approve_box[0] + 78, button_y + 28), "承認済み" if approved else "承認する", 17, WHITE if approved else FOREST, anchor="mm")
    if approved:
        cx, cy = approve_box[0] + 26, button_y + 28
        draw.line((cx - 7, cy, cx - 1, cy + 7, cx + 10, cy - 8), fill=WHITE, width=4, joint="curve")
    return approve_box


def draw_library(image):
    draw = ImageDraw.Draw(image)
    x, y, width = draw_window(image, "ナレッジ")
    draw_ui_title(draw, x, y, width, "ナレッジ一覧", "確認済み")
    rounded(draw, (x, y + 51, x + width, y + 105), 17, WHITE, outline=BORDER, width=1)
    label(draw, (x + 21, y + 79), "知識や記録を検索", 18, "#9ca596", anchor="lm")
    draw.ellipse((x + width - 45, y + 64, x + width - 29, y + 80), outline=FOREST, width=3)
    draw.line((x + width - 32, y + 78, x + width - 23, y + 87), fill=FOREST, width=3)
    for index, tag in enumerate(("工程", "判断基準", "安全")):
        tx = x + index * 124
        rounded(draw, (tx, y + 119, tx + 108, y + 161), 19, SOFT_GREEN if index == 1 else "#f8f9f2", outline=None if index == 1 else BORDER)
        label(draw, (tx + 54, y + 140), tag, 16, FOREST if index == 1 else MUTED, anchor="mm")

    rows = [
        ("作業前のチェックポイント", "承認済み  ·  工程 / 判断基準", "知識"),
        ("異常時の確認と報告", "承認済み  ·  安全 / 設備", "知識"),
        ("作業手順書.pdf", "ドキュメント  ·  取り込み完了", "文書"),
    ]
    for index, (title, detail, kind) in enumerate(rows):
        row_y = y + 173 + index * 132
        rounded(draw, (x, row_y, x + width, row_y + 113), 18, WHITE, outline=BORDER, width=1)
        rounded(draw, (x + 17, row_y + 23, x + 69, row_y + 75), 15, SOFT_YELLOW if kind == "文書" else SOFT_GREEN)
        label(draw, (x + 43, row_y + 49), "D" if kind == "文書" else "K", 19, FOREST, anchor="mm", bold=True)
        label(draw, (x + 88, row_y + 32), title, 20, INK, stroke=1)
        label(draw, (x + 88, row_y + 72), detail, 15, MUTED)
        rounded(draw, (x + width - 90, row_y + 36, x + width - 16, row_y + 76), 17, SOFT_YELLOW if kind == "文書" else SOFT_GREEN)
        label(draw, (x + width - 53, row_y + 56), kind, 15, FOREST, anchor="mm")


def left_copy(image, tag, title_lines, subtitle, caption, dark=False):
    draw = ImageDraw.Draw(image)
    chip_bg = "#314e34" if dark else SOFT_YELLOW
    chip_fg = CITRUS if dark else FOREST
    primary = WHITE if dark else FOREST
    secondary = "#cbd9c7" if dark else MUTED
    rule = CITRUS if dark else LEAF
    rounded(draw, (112, 203, 264, 249), 21, chip_bg)
    label(draw, (188, 226), tag, 18, chip_fg, anchor="mm")
    y = 287
    for line in title_lines:
        label(draw, (110, y), line, 61, primary, stroke=1)
        y += 81
    label(draw, (114, y + 8), subtitle, 25, secondary)
    draw.rounded_rectangle((114, 670, 695, 676), radius=3, fill=rule)
    label(draw, (114, 720), caption[0], 28, CITRUS if dark else FOREST, stroke=1)
    if len(caption) > 1:
        label(draw, (114, 766), caption[1], 23, secondary)


def make_intro():
    image = base_scene(dark=True)
    draw = ImageDraw.Draw(image, "RGBA")
    for offset, alpha in ((0, 18), (28, 10), (56, 5)):
        paths = [
            cubic((990, 280 + offset), (1190, 390 + offset), (1265, 505 + offset), (1470, 525 + offset)),
            cubic((1470, 525 + offset), (1675, 545 + offset), (1680, 355 + offset), (1900, 268 + offset)),
            cubic((990, 530 - offset), (1190, 420 - offset), (1265, 300 - offset), (1470, 284 - offset)),
            cubic((1470, 284 - offset), (1675, 267 - offset), (1680, 445 - offset), (1900, 535 - offset)),
        ]
        for path in paths:
            draw.line(path, fill=(232, 206, 77, alpha), width=3)
    draw_logo(draw, 1470, 240, 88, 50, dark=True)
    draw_knot(draw, 1516, 515, 185, CITRUS, 5)
    draw_logo(draw, 150, 150, 78, 47, dark=True)
    label(draw, (157, 390), "現場の知恵を、", 82, WHITE, stroke=1)
    label(draw, (157, 495), "みんなの知識へ。", 82, WHITE, stroke=1)
    label(draw, (161, 642), "AIインタビューから、確認できるナレッジへ。", 29, "#d9e3d3")
    rounded(draw, (160, 735, 675, 799), 29, CITRUS)
    label(draw, (417, 767), "AIインタビュー × 知識整理", 22, FOREST, anchor="mm")
    return image


def make_light(tag, title, subtitle, caption, renderer):
    image = base_scene()
    draw = ImageDraw.Draw(image)
    draw_brand(draw)
    left_copy(image, tag, title, subtitle, caption)
    renderer(image)
    label(ImageDraw.Draw(image), (1716, 925), "画面イメージ", 15, MUTED)
    return image


def make_approval():
    pending = base_scene(dark=True)
    draw_brand(ImageDraw.Draw(pending), dark=True)
    left_copy(pending, "確かめる", ["AIの提案は、", "人が確かめてから。"], "修正や承認を経て、チームで使える知識に。", ("提案を確かめ、必要なら修正。", "承認した内容を、チームの知識へ。"), dark=True)
    draw_review(pending, approved=False)
    label(ImageDraw.Draw(pending), (1716, 925), "画面イメージ", 15, "#aabca7")
    approved = pending.copy()
    draw_review(approved, approved=True)
    label(ImageDraw.Draw(approved), (1716, 925), "画面イメージ", 15, "#aabca7")
    return pending, approved


def make_outro():
    image = base_scene(dark=True)
    draw = ImageDraw.Draw(image, "RGBA")
    for i, alpha in enumerate((14, 24, 34)):
        yy = 370 + i * 15
        draw.line(cubic((1040, yy), (1220, yy + 70), (1295, yy + 195), (1490, yy + 220)), fill=(232, 206, 77, alpha), width=4)
        draw.line(cubic((1900, yy), (1720, yy + 70), (1645, yy + 195), (1450, yy + 220)), fill=(135, 167, 90, alpha), width=4)
        draw.line(cubic((1040, yy + 220), (1220, yy + 150), (1295, yy + 25), (1490, yy)), fill=(135, 167, 90, alpha), width=4)
        draw.line(cubic((1900, yy + 220), (1720, yy + 150), (1645, yy + 25), (1450, yy)), fill=(232, 206, 77, alpha), width=4)
    draw_logo(draw, 150, 150, 78, 47, dark=True)
    draw_logo(draw, 1490, 280, 88, 50, dark=True)
    draw_knot(draw, 1536, 555, 185, CITRUS, 5)
    label(draw, (157, 420), "知恵をつなぎ、", 78, WHITE, stroke=1)
    label(draw, (157, 520), "次の一歩へ。", 78, WHITE, stroke=1)
    label(draw, (161, 660), "AIインタビュー / ナレッジ構造化アプリ", 28, "#d9e3d3")
    rounded(draw, (160, 748, 768, 816), 30, CITRUS)
    label(draw, (464, 782), "MUSUBI   ·   現場の知恵を、みんなの知識へ。", 21, FOREST, anchor="mm")
    return image


def to_bgr(image):
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def zoom(frame, amount):
    if amount < 0.0001:
        return frame
    width = int(W * (1 + amount))
    height = int(H * (1 + amount))
    scaled = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
    x = (width - W) // 2
    y = (height - H) // 2
    return scaled[y : y + H, x : x + W]


def motion(frame, index, local_time):
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    if index in (0, 5):
        cx, cy = 1560, 520
        for i in range(3):
            angle = local_time * 1.25 + i * 2 * math.pi / 3
            px = cx + math.cos(angle) * 157
            py = cy + math.sin(angle) * 70
            pulse = 7 + 2 * math.sin(local_time * 3 + i)
            draw.ellipse((px - pulse, py - pulse, px + pulse, py + pulse), fill=(232, 206, 77, 230))
    elif index == 1:
        base_x, base_y = 1370, 794
        for i in range(22):
            amplitude = 4 + 13 * abs(math.sin(local_time * 5.3 + i * 0.63))
            x = base_x + i * 13
            draw.rounded_rectangle((x, base_y - amplitude, x + 5, base_y + amplitude), radius=3, fill=(135, 167, 90, 230))
    elif index == 2:
        phase = (local_time * 0.34) % 1.0
        x = 1150 + int(560 * phase)
        y = 575 + int(205 * phase)
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(232, 206, 77, 235))
    elif index == 3:
        pulse = 0.2 + 0.17 * (0.5 + 0.5 * math.sin(local_time * 4))
        x1, y1, x2, y2 = 1654, 675, 1821, 740
        draw.rounded_rectangle((x1 - 5, y1 - 5, x2 + 5, y2 + 5), radius=20, outline=(232, 206, 77, int(255 * pulse)), width=4)
    elif index == 4:
        phase = (local_time * 0.2) % 1.0
        x = 1160 + int(550 * phase)
        y = 660 + int(145 * phase)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(232, 206, 77, 230))
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def timestamp(seconds):
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_sidecars():
    boundaries = [0.0, 4.4, 9.5, 14.6, 19.7, 24.8, 30.0]
    captions = [
        "現場の知恵を、みんなの知識へ。\nAIインタビューから、確認できるナレッジへ。",
        "経験の“なぜ”まで、対話で聞く。\nテキスト・音声で、判断の背景を引き出す。",
        "会話から、知識候補を整理。\n回答を、確認しやすい構造へ。",
        "AIの提案は、人が確かめてから。\n修正や承認を経て、チームの知識へ。",
        "確認した知識を、チームで活用。\nタグやドキュメントとあわせて整理。",
        "知恵をつなぎ、次の一歩へ。\nMUSUBI — AIインタビュー / ナレッジ構造化",
    ]
    blocks = [f"{i + 1}\n{timestamp(boundaries[i])} --> {timestamp(boundaries[i + 1])}\n{text}\n" for i, text in enumerate(captions)]
    CAPTIONS.write_text("\n".join(blocks), encoding="utf-8")
    EMBED.write_text(
        '''<div class="musubi-video">
  <video controls playsinline preload="metadata" poster="MUSUBI_PR_poster_landscape.png">
    <source src="MUSUBI_PR_30sec_landscape.mp4" type="video/mp4">
  </video>
</div>

<style>
  .musubi-video { width: 100%; max-width: 1200px; aspect-ratio: 16 / 9; margin-inline: auto; background: #122f22; }
  .musubi-video video { display: block; width: 100%; height: 100%; object-fit: contain; }
</style>
''',
        encoding="utf-8",
    )


def render():
    intro = make_intro()
    interview = make_light("聞く", ["経験の“なぜ”まで、", "対話で聞く。"], "判断の背景を、テキスト・音声で引き出す。", ("経験者の声から、判断の背景まで。",), draw_interview)
    proposals = make_light("整理する", ["会話から、", "知識候補を整理。"], "回答を、確認しやすい構造へ。", ("会話から、確認しやすい知識候補へ。",), draw_proposals)
    pending, approved = make_approval()
    library = make_light("活用する", ["確認した知識を、", "チームで活用。"], "タグやドキュメントとあわせて整理。", ("記録や文書も整理し、知識を共有。",), draw_library)
    outro = make_outro()

    scenes = [intro, interview, proposals, pending, library, outro]
    durations = [4.4, 5.1, 5.1, 5.1, 5.1, 5.2]
    starts = np.cumsum([0.0] + durations[:-1]).tolist()
    arrays = [to_bgr(scene) for scene in scenes]
    approved_array = to_bgr(approved)
    total_frames = round(sum(durations) * FPS)
    writer = cv2.VideoWriter(str(OUTPUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open an MP4 video writer")

    scene_index = 0
    transition = 0.36
    for frame_number in range(total_frames):
        time_sec = frame_number / FPS
        while scene_index < len(durations) - 1 and time_sec >= starts[scene_index] + durations[scene_index]:
            scene_index += 1
        local_time = time_sec - starts[scene_index]
        progress = min(1.0, max(0.0, local_time / durations[scene_index]))
        current = zoom(arrays[scene_index], 0.012 * progress)

        if scene_index == 3:
            alpha = min(1.0, max(0.0, (local_time - 2.3) / 0.45))
            alpha = alpha * alpha * (3 - 2 * alpha)
            current = cv2.addWeighted(current, 1 - alpha, approved_array, alpha, 0)

        remaining = starts[scene_index] + durations[scene_index] - time_sec
        if scene_index < len(scenes) - 1 and remaining <= transition:
            next_image = arrays[scene_index + 1]
            if scene_index + 1 == 3:
                next_image = arrays[3]
            alpha = min(1.0, max(0.0, 1 - remaining / transition))
            alpha = alpha * alpha * (3 - 2 * alpha)
            current = cv2.addWeighted(current, 1 - alpha, next_image, alpha, 0)

        current = motion(current, scene_index, local_time)
        writer.write(current)
        if frame_number % (FPS * 5) == 0:
            print(f"Rendered {time_sec:05.1f}s / 30.0s", flush=True)

    writer.release()
    intro.save(POSTER)
    write_sidecars()
    print(f"Video: {OUTPUT}")
    print(f"Poster: {POSTER}")
    print(f"Captions: {CAPTIONS}")
    print(f"Embed example: {EMBED}")


if __name__ == "__main__":
    render()
