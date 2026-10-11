"""Retrieve attributed multi-franchise facts without assuming a creator's domain."""

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..security.network import validate_https_url
from ..storage_lock import interprocess_lock
from .video_analysis_config import atomic_json

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_PATH = ROOT / "config" / "media_knowledge.json"
REFERENCE_DIR = ROOT / "knowledge" / "images"
LOOKUP_DIR = ROOT / "output" / "knowledge_lookups"
KNOWLEDGE_VERSION = "1"


def normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value).casefold())


def validate_entries(entries: list) -> list:
    if not isinstance(entries, list) or len(entries) > 2000:
        raise ValueError("知识条目应为不超过 2000 项的列表")
    clean = []
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("知识条目必须为对象")
        current_id = str(entry.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", current_id) or current_id in ids:
            raise ValueError("知识条目 id 无效或重复")
        ids.add(current_id)
        name = str(entry.get("name", "")).strip()
        text = str(entry.get("text", "")).strip()
        source = str(entry.get("source_url", "")).strip()
        if not name or len(name) > 200 or not text or len(text) > 6000:
            raise ValueError("知识条目需要名称与资料正文，且不能过长")
        validate_https_url(source)
        aliases = entry.get("aliases", [])
        refs = entry.get("reference_images", [])
        if not isinstance(aliases, list) or len(aliases) > 40 or any(not isinstance(a, str) or not a.strip() or len(a) > 200 for a in aliases):
            raise ValueError("别名列表格式不正确")
        if not isinstance(refs, list) or len(refs) > 5:
            raise ValueError("参考图最多 5 张")
        for ref in refs:
            reference_path(ref)
        clean.append({
            "id": current_id, "name": name, "work": str(entry.get("work", ""))[:200],
            "kind": str(entry.get("kind", "character"))[:50], "aliases": aliases,
            "text": text, "source_url": source, "reference_images": refs,
            "variant": str(entry.get("variant", "默认形象"))[:200],
            "appearance": str(entry.get("appearance", ""))[:2000],
            "updated_at": str(entry.get("updated_at", ""))[:50], "status": "curated",
        })
    return clean


def reference_path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("参考图必须是 knowledge/images 内的相对路径")
    path = (REFERENCE_DIR / value).resolve()
    if not path.is_relative_to(REFERENCE_DIR.resolve()) or Path(value).is_absolute() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("参考图地址超出知识库图片目录")
    return path


class MediaKnowledge:
    def __init__(self):
        try:
            data = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
            self.entries = validate_entries(data.get("entries", []))
        except FileNotFoundError:
            self.entries = []
        except (OSError, ValueError, AttributeError) as exc:
            raise RuntimeError("多作品知识库无法读取，请检查条目与来源") from exc

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def retrieve(self, queries: list[str], limit: int = 6) -> list[dict]:
        needles = [normalize(q) for q in queries if isinstance(q, str) and len(normalize(q)) >= 2]
        scored = []
        for entry in self.entries:
            names = [normalize(n) for n in [entry["name"], *entry["aliases"]]]
            # Require a name/alias match; a shared franchise alone must not return every character.
            score = sum(4 if q in names else 1 for q in needles if any(n in q or q in n for n in names if len(n) >= 2))
            if score:
                scored.append((score, entry))
        return [e for _, e in sorted(scored, key=lambda pair: (-pair[0], pair[1]["id"]))[:limit]]

    @staticmethod
    def lookup(query: str) -> tuple[list[dict], str]:
        """Bounded public encyclopedia lookup. Results remain unverified candidates."""
        query = query.strip()[:150]
        if not query:
            return [], ""
        key = hashlib.sha256(query.encode()).hexdigest()
        path = LOOKUP_DIR / f"{key}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            ttl = 600 if cached.get("error") else 7 * 86400
            if cached.get("query") == query and time.time() - cached.get("saved_at", 0) < ttl:
                return cached.get("entries", []), cached.get("error", "")
        except (OSError, ValueError, TypeError):
            pass
        errors = []
        for lang in ("zh", "en"):
            endpoint = f"https://{lang}.wikipedia.org/w/api.php"
            try:
                response = requests.get(endpoint, params={
                    "action": "query", "format": "json", "generator": "search",
                    "gsrsearch": query, "gsrlimit": 2, "prop": "extracts",
                    "exintro": 1, "explaintext": 1, "exchars": 1800,
                }, headers={"User-Agent": "CyberIntelligenceAgency/0.1 (local video context retrieval)"}, timeout=(4, 6), allow_redirects=False)
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                data = response.json()
                entries = []
                for page in (data.get("query", {}).get("pages", {}) or {}).values():
                    title, extract = str(page.get("title", "")), str(page.get("extract", ""))
                    if title and extract:
                        entries.append({"id": f"wiki-{lang}-{page.get('pageid', '')}", "name": title,
                            "text": extract[:1800], "source_url": f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                            "status": "unverified_lookup", "query": query})
                if entries:
                    atomic_json(path, {"query": query, "saved_at": time.time(), "entries": entries})
                    return entries, ""
            except (requests.RequestException, ValueError, RuntimeError, AttributeError) as exc:
                errors.append(f"{lang} 资料检索失败：{type(exc).__name__}")
        error = "；".join(errors) or "百科未找到相关资料"
        atomic_json(path, {"query": query, "saved_at": time.time(), "entries": [], "error": error})
        return [], error


def save_entries(entries: list) -> dict:
    result = {"version": 1, "entries": validate_entries(entries)}
    with interprocess_lock(KNOWLEDGE_PATH):
        atomic_json(KNOWLEDGE_PATH, result)
    return result


def append_entries(entries: list) -> dict:
    """Merge under the file lock so two uploads cannot erase each other."""
    incoming = validate_entries(entries)
    with interprocess_lock(KNOWLEDGE_PATH):
        current = MediaKnowledge().entries
        by_id = {entry["id"]: entry for entry in current}
        for entry in incoming:
            if entry["id"] in by_id:
                raise ValueError("条目 id 已存在，请使用新的 id")
            by_id[entry["id"]] = entry
        result = {"version": 1, "entries": validate_entries(list(by_id.values()))}
        atomic_json(KNOWLEDGE_PATH, result)
    return result
