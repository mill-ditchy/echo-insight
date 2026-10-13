import unittest
from unittest.mock import Mock, patch

from src.fetcher import fetch_metadata, select_recent_unique_articles
from src.state_manager import get_new_articles
from src.platforms.bilibili import BilibiliFetcher
import scheduler


def article(current_id: str) -> dict:
    return {
        "id": current_id,
        "article_id": current_id,
        "title": current_id,
        "link": f"https://example.com/{current_id}",
    }


class RecentContentWindowTests(unittest.TestCase):
    def test_keeps_only_latest_three_articles(self) -> None:
        selected = select_recent_unique_articles(
            [article("5"), article("4"), article("3"), article("2"), article("1")]
        )

        self.assertEqual([item["id"] for item in selected], ["5", "4", "3"])

    def test_duplicate_inside_results_is_removed_before_limit(self) -> None:
        selected = select_recent_unique_articles(
            [article("5"), article("5"), article("4"), article("3"), article("2")]
        )

        self.assertEqual([item["id"] for item in selected], ["5", "4", "3"])

    def test_resume_filters_previous_items_without_backfilling_older_content(self) -> None:
        current = [article("5"), article("4"), article("3")]
        for item in current:
            item.update({"platform": "zhihu", "source_uid": "people:demo"})
        state = {
            "version": 1,
            "sources": {
                "zhihu:people:demo": {"seen_ids": ["4", "2", "1"]},
            },
        }

        new_articles = get_new_articles(current, state)

        self.assertEqual([item["id"] for item in new_articles], ["5", "3"])

    def test_fetch_metadata_limits_each_source_independently(self) -> None:
        sources = [
            {"name": "甲", "platform": "bilibili", "uid": "1", "category": "B站"},
            {"name": "乙", "platform": "bilibili", "uid": "2", "category": "B站"},
        ]
        fetcher = BilibiliFetcher()
        fetcher.fetch = Mock(side_effect=[
            [article("a5"), article("a4"), article("a3"), article("a2")],
            [article("b5"), article("b4"), article("b3"), article("b2")],
        ])
        with (
            patch("src.state_manager.load_state", return_value={"sources": {}}),
            patch("src.collection_state.get_scan", return_value={}),
            patch("src.collection_state.record_scan"),
            patch("src.fetcher.list_sources", return_value=sources),
            patch("src.fetcher.get_fetcher", return_value=fetcher),
        ):
            fetched, errors = fetch_metadata()

        self.assertEqual(errors, [])
        self.assertEqual(
            [item["id"] for item in fetched],
            ["a5", "a4", "a3", "b5", "b4", "b3"],
        )
        self.assertEqual([item["source"] for item in fetched], ["甲"] * 3 + ["乙"] * 3)

    def test_first_detection_saves_three_previews_per_source(self) -> None:
        articles = []
        for source_uid in ("1", "2"):
            for current_id in ("5", "4", "3"):
                item = article(f"{source_uid}-{current_id}")
                item.update({"platform": "bilibili", "source_uid": source_uid})
                articles.append(item)
        with patch.object(
            scheduler,
            "record_baseline_preview",
            side_effect=lambda items: items,
        ) as record_preview:
            count = scheduler._record_first_detection_previews(articles)

        self.assertEqual(count, 6)
        self.assertEqual(record_preview.call_count, 2)
        self.assertTrue(all(len(call.args[0]) == 3 for call in record_preview.call_args_list))


if __name__ == "__main__":
    unittest.main()
