"""Author the three owned development fixtures; never run during test collection.

The checked-in PNGs, not a rerender using another font/runtime, define the inputs.
No game artwork or font file is redistributed. This is synthetic developer data,
not model output, real-game evidence, or a held-out quality evaluation dataset.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

import PIL
import yaml
from PIL import Image, ImageDraw, ImageFont


def build(font_path: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    font_hash = hashlib.sha256(font_path.read_bytes()).hexdigest()
    cases, annotations = [], {}
    definitions = [
        ("hud-en-landscape", (1280, 720), "en", "landscape"),
        ("hud-zh-dense", (1440, 900), "zh", "landscape"),
        ("hud-portrait", (720, 1280), "zh", "portrait"),
    ]
    for index, (identity, size, language, orientation) in enumerate(definitions):
        width, height = size
        image = Image.new("RGB", size)
        draw = ImageDraw.Draw(image)
        rng = random.Random(13002 + index)
        for y in range(height):
            draw.line((0, y, width, y), fill=(15 + y * 12 // height, 35 + y * 35 // height, 54))
        for _ in range(90):
            x, y = rng.randrange(width), rng.randrange(height)
            radius = rng.randrange(12, 55)
            draw.ellipse((x, y, x + radius, y + radius), fill=(32, rng.randrange(60, 95), 69))
        texts, elements = [], []

        def panel(
            element_id,
            box,
            label,
            *,
            kind="panel",
            font_size=22,
            fill=(22, 30, 43),
            draw=draw,
            elements=elements,
            texts=texts,
            identity=identity,
        ):
            draw.rounded_rectangle(box, radius=8, fill=fill, outline=(116, 145, 161), width=2)
            elements.append({"id": element_id, "kind": kind, "bbox": list(box)})
            if label:
                font = ImageFont.truetype(str(font_path), font_size)
                position = (box[0] + 12, box[1] + 10)
                bbox = list(draw.textbbox(position, label, font=font, anchor="lt"))
                if bbox[2] > box[2] - 6 or bbox[3] > box[3] - 6:
                    raise ValueError(f"Label exceeds panel: {identity}/{element_id}")
                draw.text(position, label, font=font, fill=(243, 238, 212), anchor="lt")
                texts.append({"id": f"text-{element_id}", "text": label, "bbox": bbox, "element_id": element_id})

        if identity == "hud-en-landscape":
            panel("status", (24, 24, 330, 112), "HEALTH  85 / 100", kind="bar", font_size=26)
            draw.rectangle((40, 82, 294, 96), fill=(58, 179, 113))
            panel("mission", (410, 24, 810, 84), "DEFEND THE OUTPOST", font_size=24)
            panel("minimap", (1024, 24, 1256, 250), "NORTH", kind="minimap")
            draw.line((1035, 195, 1100, 135, 1180, 195, 1240, 110), fill=(233, 172, 92), width=4)
            draw.ellipse((1134, 120, 1148, 134), fill=(84, 206, 234))
            panel("objective", (24, 390, 355, 482), "OBJECTIVE  02 / 03", font_size=23)
            panel("ammo", (954, 590, 1256, 691), "AMMO  24 / 120", kind="counter", font_size=27)
            for number, label in enumerate(["Q  SHIELD", "E  HEAL", "R  RELOAD"]):
                panel(
                    f"action-{number}",
                    (340 + 190 * number, 624, 518 + 190 * number, 689),
                    label,
                    kind="button",
                    font_size=19,
                )
            draw.line((626, 360, 654, 360), fill="white", width=2)
            draw.line((640, 346, 640, 374), fill="white", width=2)
            elements.append({"id": "crosshair", "kind": "crosshair", "bbox": [625, 345, 656, 376]})
        elif identity == "hud-zh-dense":
            panel("header", (24, 20, 1416, 86), "远征指挥室   第十二回合   资源：金币 1250  木材 680", font_size=27)
            for column, labels in enumerate(
                [
                    [
                        "任务列表",
                        "守住北侧入口",
                        "建造防御工事",
                        "收集五份补给",
                        "护送侦察小队",
                        "奖励：经验 350",
                        "倒计时 02:45",
                    ],
                    [
                        "部队状态",
                        "步兵  80 / 100",
                        "弓兵  60 / 80",
                        "骑兵  24 / 30",
                        "法师  12 / 20",
                        "士气：稳定",
                        "补给：充足",
                    ],
                    [
                        "战斗日志",
                        "发现敌方巡逻队",
                        "防御塔建造完成",
                        "治疗技能已就绪",
                        "侦察报告已送达",
                        "天气：多云",
                        "地形：丘陵",
                    ],
                ]
            ):
                for row, label in enumerate(labels):
                    x, y = 26 + column * 465, 140 + row * 74
                    panel(f"row-{column}-{row}", (x, y, x + 435, y + 60), label, font_size=22)
            for number, label in enumerate(["编队", "建造", "科技", "背包", "地图", "结束回合"]):
                x = 26 + number * 233
                panel(f"action-{number}", (x, 780, x + 215, 866), label, kind="button", font_size=24)
        else:
            panel("header", (20, 22, 700, 89), "星港探索   LEVEL 08", font_size=28)
            panel("energy", (20, 108, 338, 164), "能量  68 / 100", kind="bar", font_size=23)
            panel("credits", (365, 108, 700, 164), "金币  2350", kind="counter", font_size=23)
            panel("minimap", (462, 190, 700, 440), "区域 A-03", kind="minimap")
            draw.line((480, 390, 520, 330, 570, 365, 660, 280), fill=(228, 173, 105), width=5)
            panel("mission", (20, 475, 700, 540), "当前目标：找到能源核心", font_size=26)
            for row, label in enumerate(["探索进度  3 / 5", "护盾  120", "氧气剩余  04:20"]):
                panel(f"status-{row}", (20, 595 + row * 80, 405, 659 + row * 80), label, font_size=24)
            panel("confirm", (140, 955, 580, 1041), "开始探索", kind="button", font_size=30)
            for number, label in enumerate(["任务", "装备", "地图", "设置"]):
                x = 20 + number * 174
                panel(f"tab-{number}", (x, 1140, x + 158, 1245), label, kind="button", font_size=26)
        path = output / f"{identity}.png"
        image.save(path, format="PNG", optimize=False)
        cases.append(
            {
                "case_id": identity,
                "group_id": f"synthetic-{index + 1}",
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": list(size),
                "language": language,
                "orientation": orientation,
                "split": "development",
                "source_kind": "original_synthetic_ui",
                "license_id": "project-owned-test-fixture",
                "known_background": False,
            }
        )
        annotations[identity] = {
            "coordinate_space": "canonical_px_xyxy_exclusive_max",
            "texts": texts,
            "elements": elements,
        }
    manifest = {
        "schema_version": 1,
        "acceptance_status": "development_only",
        "generator_version": 1,
        "pillow_version": PIL.__version__,
        "font_sha256": font_hash,
        "cases": cases,
    }
    (output / "preview-cases.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (output / "preview-annotations.json").write_text(
        json.dumps({"schema_version": 1, "cases": annotations}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True, help="Installed CJK-capable font; never copied")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    build(args.font, args.output)
