"""信息源管理器 — 用户不需要知道 RSS、不需要碰 JSON 文件。"""

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .platforms.registry import PLATFORMS, get_all_platforms
from .storage_lock import interprocess_lock

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.json"
_ZHIHU_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_source_lock = threading.RLock()


def _load() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("sources", []), list):
                return data
            raise ValueError("sources字段不是列表")
    except FileNotFoundError:
        return {"version": 1, "sources": []}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"关注列表无法读取，已停止写入以保护原数据：{CONFIG_PATH}"
        ) from exc
    raise RuntimeError(f"关注列表格式无效，已停止写入：{CONFIG_PATH}")


def _save(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CONFIG_PATH.with_name(
        f"{CONFIG_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(CONFIG_PATH)


# ========== 公开接口 ==========

def normalize_source_uid(platform: str, value: str) -> str:
    """把用户输入转换成稳定、可用于URL路径的内部标识。"""
    platform = str(platform).strip().lower()
    value = str(value).strip().rstrip("/")
    if platform == "douyin":
        from .platforms.douyin import normalize_uid
        return normalize_uid(value)
    if platform == "xiaohongshu":
        from .platforms.xiaohongshu import normalize_uid
        return normalize_uid(value)
    if platform != "zhihu":
        return value

    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"zhihu.com", "www.zhihu.com"}:
            raise ValueError("请填写 zhihu.com 的个人主页或机构主页链接")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"people", "org"}:
            token = parts[1]
            if _ZHIHU_TOKEN_PATTERN.fullmatch(token):
                return f"{parts[0]}:{token}"
        raise ValueError("请填写知乎个人主页或机构主页链接")
    if ":" in value:
        account_type, _, token = value.partition(":")
        if (
            account_type in {"people", "org", "auto"}
            and _ZHIHU_TOKEN_PATTERN.fullmatch(token)
        ):
            return f"{account_type}:{token}"
        raise ValueError("知乎账号格式无效")
    if not value:
        raise ValueError("知乎主页链接或网址标识不能为空")
    if not _ZHIHU_TOKEN_PATTERN.fullmatch(value):
        raise ValueError(
            "这里不能填写中文昵称。请粘贴完整知乎主页链接，或填写网址中 "
            "/people/、/org/ 后面的英文标识"
        )
    return f"auto:{value}"

def add_source(platform: str, uid: str, name: str | None = None, *, profile_url: str = "", avatar: str = "") -> bool:
    """添加一个关注。"""
    info = PLATFORMS.get(platform)
    if not info:
        print(f"不支持的平台：{platform}")
        return False

    try:
        uid = normalize_source_uid(platform, uid)
    except ValueError as exc:
        print(str(exc))
        return False
    if not uid:
        print("UID 不能为空。")
        return False
    if platform == "xiaohongshu" and profile_url and normalize_source_uid(platform, profile_url) != uid:
        raise ValueError("主页链接与账号ID不一致")

    with _source_lock, interprocess_lock(CONFIG_PATH):
        data = _load()
        sources = data.get("sources", [])

        # 避免重复（同平台 + 同 UID）
        for s in sources:
            if s.get("platform") == platform and s.get("uid") == uid:
                print("已存在，跳过。")
                return True

        display_name = name or f"{info['label']} - {uid}"
        sources.append({
            "name": display_name,
            "platform": platform,
            "uid": uid,
            "category": info["category"],
            "added_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **({"profile_url": profile_url, "avatar": avatar} if platform == "xiaohongshu" else {}),
        })
        data["sources"] = sources
        _save(data)

    print(f"[添加成功] {display_name}")
    return True


def list_sources() -> list[dict]:
    """列出当前所有关注。"""
    return _load().get("sources", [])


def get_source(platform: str, uid: str) -> dict | None:
    """按平台和UID查找一个关注。"""
    return next(
        (
            source
            for source in list_sources()
            if source.get("platform") == platform and str(source.get("uid")) == str(uid)
        ),
        None,
    )


def delete_source(platform: str, uid: str) -> dict | None:
    """按平台和UID删除关注，并返回被删除的记录。"""
    platform = str(platform).strip().lower()
    uid = str(uid).strip()
    with _source_lock, interprocess_lock(CONFIG_PATH):
        data = _load()
        sources = data.get("sources", [])
        remaining = []
        removed = None

        for source in sources:
            is_target = (
                source.get("platform") == platform
                and str(source.get("uid", "")) == uid
            )
            if is_target and removed is None:
                removed = source
            else:
                remaining.append(source)

        if removed is None:
            return None
        data["sources"] = remaining
        _save(data)
        return removed


def remove_source(index: int) -> bool:
    """按序号删除关注。"""
    with _source_lock, interprocess_lock(CONFIG_PATH):
        data = _load()
        sources = data.get("sources", [])
        if index < 0 or index >= len(sources):
            return False
        removed = sources.pop(index)
        data["sources"] = sources
        _save(data)
    print(f"[已删除] {removed['name']}")
    return True


def interactive_add():
    """交互式添加关注。"""
    print("\n" + "=" * 40)
    print("  添加新的关注")
    print("=" * 40)

    platforms = get_all_platforms()
    print("\n可选平台：")
    for i, p in enumerate(platforms, 1):
        note_str = f"  — {p['note']}" if p["note"] else ""
        print(f"  {i}. {p['label']}{note_str}")
    print(f"  {len(platforms) + 1}. 取消")

    choice = input("\n请选择编号：").strip()
    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(platforms):
            print("已取消。")
            return
    except ValueError:
        print("输入无效，已取消。")
        return

    platform_key = platforms[idx]["key"]
    rule = PLATFORMS[platform_key]

    uid = input(f"\n{rule['uid_prompt']}\n例如：{rule['uid_example']}\n> ").strip()
    if not uid:
        print("UID 不能为空，已取消。")
        return

    name_input = input("\n给它起个名字（直接回车使用默认名）：").strip()
    add_source(platform_key, uid, name_input or None)


def interactive_list():
    """交互式查看关注列表。"""
    sources = list_sources()
    if not sources:
        print("\n当前还没有关注任何人，先添加一个吧！")
        return

    print("\n" + "=" * 40)
    print("  当前关注列表")
    print("=" * 40)
    for i, s in enumerate(sources, 1):
        print(f"  {i}. [{s['category']}] {s['name']}")

    print("\n输入编号可以删除，直接回车返回。")
    choice = input("> ").strip()
    if choice:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sources):
                remove_source(idx)
        except ValueError:
            pass
