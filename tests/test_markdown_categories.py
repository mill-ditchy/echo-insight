import unittest

from src.fetcher import format_as_markdown


class MarkdownCategoryTests(unittest.TestCase):
    def test_groups_same_zhihu_source_by_content_type(self) -> None:
        markdown = format_as_markdown([
            {
                "platform": "zhihu",
                "category": "知乎",
                "source": "知乎日报",
                "title": "文章一",
                "link": "https://zhuanlan.zhihu.com/p/1",
                "published": "2026-08-12",
                "summary": "文章摘要",
                "content_type_label": "文章",
                "action": "发表了文章",
                "ai_status": "not_applicable",
            },
            {
                "platform": "zhihu",
                "category": "知乎",
                "source": "知乎日报",
                "title": "想法一",
                "link": "https://www.zhihu.com/pin/2",
                "published": "2026-08-12",
                "summary": "想法摘要",
                "content_type_label": "想法",
                "action": "发布了想法",
                "ai_status": "not_applicable",
            },
        ])

        self.assertIn("## 知乎", markdown)
        self.assertIn("### 知乎日报", markdown)
        self.assertIn("#### 文章", markdown)
        self.assertIn("#### 想法", markdown)
        self.assertIn("- 内容摘要：文章摘要", markdown)


if __name__ == "__main__":
    unittest.main()
