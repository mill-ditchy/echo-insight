"""Conservative follow-time filtering for paged platform results."""
from datetime import datetime
from ..content_identity import article_id


def follow_window(articles, boundary, anchors):
    """Do not turn pre-follow history into unread content when a page is larger than the baseline."""
    known = set(anchors)
    window = []
    for item in articles:
        if item.get("pinned"):
            continue
        window.append(item)
        if article_id(item) in known:
            break
    if boundary:
        conservative = {article_id(item) for item in window}
        def timestamp(item):
            if item.get("published_ts"):
                return float(item["published_ts"])
            try:
                return datetime.fromisoformat(str(item.get("published") or "")).timestamp()
            except (ValueError, TypeError, OSError):
                return 0
        return [item for item in articles if timestamp(item) >= boundary or (not timestamp(item) and article_id(item) in conservative)]
    return window

