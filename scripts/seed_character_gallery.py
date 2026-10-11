"""Import attributed starter references; community mirrors, not official verification."""
import io
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.processors.media_knowledge import MediaKnowledge, append_entries, REFERENCE_DIR

WU_REV = "e69260313edc04ca9b8ca1d52fec1bb485c6c1b1"
SR_REV = "dbe8cdfcfb0bf657b9fe1cf92d5537f118ccb487"
WU = {"Jinhsi": "今汐", "Changli": "长离", "Yangyang": "秧秧", "Yinlin": "吟霖", "Sanhua": "散华", "Shorekeeper": "守岸人",
      "Camellya": "椿", "Carlotta": "珂莱塔", "Cartethyia": "卡提希娅", "Phoebe": "菲比", "Roccia": "洛可可", "Ciaccona": "夏空",
      "Cantarella": "坎特蕾拉", "Baizhi": "白芷", "Chixia": "炽霞", "Danjin": "丹瑾", "Encore": "安可", "Jianxin": "鉴心",
      "Jiyan": "忌炎", "Calcharo": "卡卡罗", "Aalto": "秋水", "Mortefi": "莫特斐", "Lingyang": "凌阳", "Lumi": "灯灯",
      "Qingxiao": "清宵", "Qiuyuan": "仇远", "Zani": "赞妮", "Zhezhi": "折枝", "XiangliYao": "相里要", "Verina": "维里奈", "Taoqi": "桃祈", "Yuanwu": "渊武", "Youhu": "釉瑚", "Brant": "布兰特",
      "Lupa": "露帕", "Phrolova": "弗洛洛", "Augusta": "奥古斯塔", "Iuno": "尤诺", "Buling": "卜灵", "Galbrena": "嘉贝莉娜", "Chisa": "千咲", "Lynae": "琳奈", "Mornye": "莫宁", "Aemeath": "爱弥斯", "Sigrika": "西格莉卡", "Hiyuki": "绯雪"}
GI = {"Hutao": ("胡桃", "Hu Tao"), "Keqing": ("刻晴", "Keqing"), "Shougun": ("雷电将军", "Raiden Shogun"), "Nahida": ("纳西妲", "Nahida"),
      "Furina": ("芙宁娜", "Furina"), "Ayaka": ("神里绫华", "Kamisato Ayaka"), "Ganyu": ("甘雨", "Ganyu"), "Zhongli": ("钟离", "Zhongli")}
SR = {"1001": ("三月七", "March 7th"), "1002": ("丹恒", "Dan Heng"), "1003": ("姬子", "Himeko"), "1005": ("卡芙卡", "Kafka"),
      "1006": ("银狼", "Silver Wolf"), "1102": ("希儿", "Seele"), "1205": ("刃", "Blade"), "1307": ("黑天鹅", "Black Swan")}


def import_one(spec):
    entry, url = spec
    response = requests.get(url, timeout=(8, 30))
    response.raise_for_status()
    if len(response.content) > 5 * 1024 * 1024:
        raise ValueError("reference too large")
    with Image.open(io.BytesIO(response.content)) as original:
        if original.width * original.height > 16_000_000:
            raise ValueError("reference dimensions too large")
        image = original.convert("RGBA")
        base = Image.new("RGBA", image.size, "white")
        base.alpha_composite(image)
        base = base.convert("RGB")
        base.thumbnail((960, 960))
        filename = entry["id"] + ".jpg"
        base.save(REFERENCE_DIR / filename, quality=94)
    entry["reference_images"] = [filename]
    return entry


def main():
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    existing = {e["id"] for e in MediaKnowledge().entries}
    specs = []
    for key, name in WU.items():
        source = f"https://github.com/ryanbenson/wuthering-waves-assets/blob/{WU_REV}/images/{key}.png"
        specs.append(({"id": "wu-" + key.lower(), "name": name, "work": "鸣潮", "kind": "character", "aliases": [key],
            "variant": "默认角色头像", "text": f"{name}（{key}）是《鸣潮》角色。参考图来自社区维护的游戏素材镜像，仅供外观核对；二创剧情和服装改变须依据视频。", "source_url": source},
            f"https://raw.githubusercontent.com/ryanbenson/wuthering-waves-assets/{WU_REV}/images/{key}.png"))
    for element in ("Spectro", "Havoc", "Aero"):
        for gender, label in (("Male", "男"), ("Female", "女")):
            key = f"Rover{element}{gender}"
            source = f"https://github.com/ryanbenson/wuthering-waves-assets/blob/{WU_REV}/images/{key}.png"
            specs.append(({"id": "wu-" + key.lower(), "name": f"漂泊者（{label}）", "work": "鸣潮", "kind": "character", "aliases": [key, f"{label}漂泊者"],
                "variant": element + "角色头像", "text": "《鸣潮》主角漂泊者的" + label + "性形象。属性与剧情须另核对，不能用头像推断二创剧情。图片来自社区素材镜像。", "source_url": source},
                f"https://raw.githubusercontent.com/ryanbenson/wuthering-waves-assets/{WU_REV}/images/{key}.png"))
    for key, (name, alias) in SR.items():
        source = f"https://github.com/Mar-7th/StarRailRes/blob/{SR_REV}/image/character_portrait/{key}.png"
        specs.append(({"id": "sr-" + key, "name": name, "work": "崩坏：星穹铁道", "kind": "character", "aliases": [alias],
            "variant": "默认角色立绘", "text": f"{name}（{alias}）是《崩坏：星穹铁道》角色。参考立绘来自社区维护的游戏素材镜像，仅供外观核对。", "source_url": source},
            f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/{SR_REV}/image/character_portrait/{key}.png"))
    for key, (name, alias) in GI.items():
        source = f"https://enka.network/ui/UI_AvatarIcon_{key}.png"
        specs.append(({"id": "gi-" + key.lower(), "name": name, "work": "原神", "kind": "character", "aliases": [alias], "variant": "默认角色头像",
            "text": f"{name}（{alias}）是《原神》角色。参考图来自Enka.Network社区游戏素材服务，仅供外观核对。", "source_url": source}, source))
    pending = [spec for spec in specs if spec[0]["id"] not in existing]
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [(spec[0]["id"], pool.submit(import_one, spec)) for spec in pending]
        for name, job in jobs:
            try:
                results.append(job.result())
                print("IMPORTED", name, flush=True)
            except Exception as exc:
                errors.append(name)
                print("FAILED", name, type(exc).__name__, flush=True)
    if results:
        append_entries(results)
    print(f"Imported {len(results)}; failed {len(errors)}", flush=True)
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
