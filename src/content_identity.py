"""Stable content and source keys shared by collection and history."""
import hashlib


def article_id(article: dict) -> str:
    explicit = article.get("video_id") or article.get("article_id") or article.get("id")
    if explicit:
        return str(explicit)
    link = article.get("link", "")
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]


def source_key(article: dict) -> str:
    return f"{article.get('platform', 'unknown')}:{article.get('source_uid', article.get('source', 'unknown'))}"

