import json
import unittest
from unittest.mock import Mock, patch

from src.platforms.zhihu import (
    ZhihuFetcher,
    parse_activities,
    parse_activity_api,
    parse_content_api,
    parse_initial_activities,
    parse_rendered_content_links,
)
from src.source_manager import normalize_source_uid


SAMPLE_PAGE = """
<html><body>
<h1>知乎日报<span>已认证账号</span></h1>
<div class="List-item">
  <div class="ActivityItem-meta">
    <span class="ActivityItem-metaTitle">发表了文章</span><span>2026-08-12 08:00</span>
  </div>
  <div class="ContentItem ArticleItem" data-zop='{"itemId":"100","title":"测试文章","type":"article"}'>
    <h2 class="ContentItem-title"><a href="//zhuanlan.zhihu.com/p/100">测试文章</a></h2>
    <div class="RichContent-inner"><span itemprop="articleBody">这是文章摘要。阅读全文</span></div>
  </div>
</div>
<div class="List-item">
  <div class="ActivityItem-meta">
    <span class="ActivityItem-metaTitle">发布了想法</span><span>2026-08-11 18:25</span>
  </div>
  <div class="ContentItem PinItem" data-zop='{"itemId":"200","type":"pin"}'>
    <div class="RichContent-inner"><span itemprop="articleBody">这是一条测试想法。</span></div>
    <a href="//www.zhihu.com/pin/200">发布于2026-08-11</a>
  </div>
</div>
<div class="List-item">
  <div class="ActivityItem-meta">
    <span class="ActivityItem-metaTitle">赞同了文章</span><span>2026-08-11 11:46</span>
  </div>
  <div class="ContentItem ArticleItem" data-zop='{"itemId":"300","title":"别人写的文章","type":"article"}'>
    <h2 class="ContentItem-title"><a href="//zhuanlan.zhihu.com/p/300">别人写的文章</a></h2>
  </div>
</div>
</body></html>
"""

SAMPLE_API = {
    "data": [
        {
            "verb": "MEMBER_CREATE_ANSWER",
            "created_time": 1786579200,
            "actor": {"name": "测试用户"},
            "target": {
                "id": 901,
                "type": "answer",
                "url": "https://www.zhihu.com/question/800/answer/901",
                "question": {"title": "测试问题"},
                "excerpt": "<p>这是回答摘要。</p>",
            },
        },
        {
            "verb": "MEMBER_VOTEUP_ANSWER",
            "target": {
                "id": 902,
                "type": "answer",
                "url": "https://www.zhihu.com/question/800/answer/902",
            },
        },
        {
            "action_text": "发表了文章",
            "actor": {"name": "测试用户"},
            "target": {
                "id": 903,
                "type": "article",
                "title": "字段兼容测试",
                "excerpt": "文章摘要",
            },
        },
    ]
}

SAMPLE_INITIAL_PAGE = f"""
<html><body><div id="root"></div>
<script id="js-initialData" type="text/json">
{json.dumps({'initialState': {'entities': {'activities': SAMPLE_API['data']}}}, ensure_ascii=False)}
</script></body></html>
"""


class ZhihuSourceInputTests(unittest.TestCase):
    def test_normalizes_people_and_org_urls(self) -> None:
        self.assertEqual(
            normalize_source_uid("zhihu", "https://www.zhihu.com/org/zhi-hu-ri-bao-51-41"),
            "org:zhi-hu-ri-bao-51-41",
        )
        self.assertEqual(
            normalize_source_uid("zhihu", "https://www.zhihu.com/people/excited-vczh/"),
            "people:excited-vczh",
        )

    def test_plain_username_uses_auto_detection(self) -> None:
        self.assertEqual(normalize_source_uid("zhihu", "excited-vczh"), "auto:excited-vczh")

    def test_rejects_display_name_instead_of_building_invalid_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能填写中文昵称"):
            normalize_source_uid("zhihu", "知乎日报")


class ZhihuActivityParserTests(unittest.TestCase):
    def test_keeps_creations_and_filters_vote_actions(self) -> None:
        name, items = parse_activities(SAMPLE_PAGE)

        self.assertEqual(name, "知乎日报")
        self.assertEqual([item["content_type_label"] for item in items], ["文章", "想法"])
        self.assertEqual(items[0]["article_id"], "article:100")
        self.assertEqual(items[0]["published"], "2026-08-12 08:00")
        self.assertEqual(items[0]["summary"], "这是文章摘要。")
        self.assertEqual(items[1]["title"], "这是一条测试想法。")
        self.assertTrue(all(item["ai_status"] == "not_applicable" for item in items))

    def test_activity_api_keeps_creations_and_filters_votes(self) -> None:
        items = parse_activity_api(SAMPLE_API)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["article_id"], "answer:901")
        self.assertEqual(items[0]["title"], "测试问题")
        self.assertEqual(items[0]["summary"], "这是回答摘要。")
        self.assertEqual(items[0]["author_name"], "测试用户")
        self.assertEqual(items[1]["article_id"], "article:903")

    def test_reads_activities_from_embedded_initial_state(self) -> None:
        items = parse_initial_activities(SAMPLE_INITIAL_PAGE)

        self.assertEqual(
            [item["article_id"] for item in items],
            ["answer:901", "article:903"],
        )

    def test_content_api_builds_answer_link(self) -> None:
        items = parse_content_api(
            {
                "data": [{
                    "id": 901,
                    "question": {"id": 800, "title": "测试问题"},
                    "excerpt": "回答摘要",
                    "author": {"name": "测试用户"},
                    "created_time": 1786579200,
                }]
            },
            "answer",
        )

        self.assertEqual(items[0]["article_id"], "answer:901")
        self.assertEqual(items[0]["link"], "https://www.zhihu.com/question/800/answer/901")

    def test_rendered_links_extract_real_name_and_answer_content(self) -> None:
        items = parse_rendered_content_links(
            [{
                "href": "https://www.zhihu.com/question/800/answer/901",
                "title": "测试问题",
                "context": (
                    "子夜极光\n回答了问题\n测试问题\n"
                    "这是浏览器渲染出的回答正文。\n发布于 2026-08-18"
                ),
            }],
            profile_name="子夜极光",
        )

        self.assertEqual(items[0]["author_name"], "子夜极光")
        self.assertEqual(items[0]["title"], "测试问题")
        self.assertIn("这是浏览器渲染出的回答正文", items[0]["summary"])

    def test_rendered_links_pin_with_only_timestamp_is_not_treated_as_content(self) -> None:
        items = parse_rendered_content_links(
            [{
                "href": "https://www.zhihu.com/pin/2073107753359382463",
                "title": "",
                "context": "发布于2026-08-18 18:33",
            }],
            profile_name="子夜极光",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content_type"], "pin")
        self.assertEqual(items[0]["title"], "")
        self.assertEqual(items[0]["summary"], "")
        self.assertEqual(items[0]["published"], "2026-08-18 18:33")

    def test_rendered_card_uses_body_and_activity_time_instead_of_timestamp_anchor(self) -> None:
        items = parse_rendered_content_links([{
            "href": "https://www.zhihu.com/pin/901",
            "title": "发布于2026-10-10 15:07",
            "context": "发布了想法\n2026-10-10 15:07\n测试用户\n已认证机构号\n正文\n赞同 2",
            "summary": "这是想法正文。阅读全文",
            "published": "2026-10-10 15:07",
            "action": "发布了想法",
        }], profile_name="测试用户")
        self.assertEqual(items[0]["title"], "这是想法正文。")
        self.assertEqual(items[0]["summary"], "这是想法正文。")
        self.assertEqual(items[0]["published"], "2026-10-10 15:07")

    def test_rendered_cards_filter_non_creation_actions(self) -> None:
        items = parse_rendered_content_links([
            {"href": "https://zhuanlan.zhihu.com/p/901", "action": "赞同了文章"},
            {"href": "https://www.zhihu.com/question/800/answer/902", "action": "收藏了回答"},
            {"href": "https://zhuanlan.zhihu.com/p/903", "action": "发表了文章", "summary": "原创"},
        ])
        self.assertEqual([item["article_id"] for item in items], ["article:903"])

    @patch("src.platforms.zhihu.load_rendered_profile")
    @patch("src.platforms.zhihu.requests.get")
    def test_browser_dom_fallback_handles_api_404s(
        self, request_get, load_rendered
    ) -> None:
        profile_response = Mock(
            status_code=200,
            text="<html><title>子夜极光 - 知乎</title></html>",
            url="https://www.zhihu.com/people/demo",
            headers={"Content-Type": "text/html"},
        )
        profile_response.raise_for_status.return_value = None
        not_found = Mock(
            status_code=404,
            text="",
            url="https://www.zhihu.com/api/v4",
            headers={"Content-Type": "text/html"},
        )
        request_get.side_effect = [profile_response, *([not_found] * 6)]
        load_rendered.return_value = {
            "html": "<html><title>子夜极光 - 知乎</title></html>",
            "profile_name": "子夜极光",
            "activity_payloads": [],
            "content_links": [{
                "href": "https://www.zhihu.com/question/800/answer/901",
                "title": "测试问题",
                "context": "子夜极光\n回答了问题\n测试问题\n回答正文",
            }],
        }

        items = ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")

        self.assertEqual(items[0]["author_name"], "子夜极光")
        self.assertEqual(items[0]["summary"], "回答正文")

    @patch("src.platforms.zhihu.load_rendered_profile")
    @patch("src.platforms.zhihu.requests.get")
    def test_profile_http_rejection_uses_browser_with_saved_session(
        self, request_get, load_rendered
    ) -> None:
        load_rendered.return_value = {
            "html": "", "profile_name": "测试用户",
            "activity_payloads": [SAMPLE_API], "content_links": [],
        }
        for status in (401, 403):
            with self.subTest(status=status):
                request_get.reset_mock()
                load_rendered.reset_mock()
                request_get.return_value = Mock(status_code=status)
                items = ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")
                self.assertEqual(items[0]["article_id"], "answer:901")
                load_rendered.assert_called_once_with("people", "demo", "z_c0=test")
                self.assertEqual(request_get.call_count, 1)

    @patch("src.platforms.zhihu.load_rendered_profile")
    @patch("src.platforms.zhihu.requests.get")
    def test_activity_http_rejection_uses_browser_without_more_api_requests(
        self, request_get, load_rendered
    ) -> None:
        request_get.side_effect = [
            Mock(status_code=200, text="<title>测试用户 - 知乎</title>",
                 url="https://www.zhihu.com/people/demo"),
            Mock(status_code=403),
        ]
        load_rendered.return_value = {
            "html": "", "activity_payloads": [SAMPLE_API], "content_links": [],
        }
        items = ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")
        self.assertEqual(items[0]["article_id"], "answer:901")
        self.assertEqual(request_get.call_count, 2)
        load_rendered.assert_called_once()

    @patch("src.platforms.zhihu.load_rendered_profile",
           side_effect=RuntimeError("请在账号管理中重新登录知乎"))
    @patch("src.platforms.zhihu.requests.get")
    def test_browser_login_failure_retains_actionable_diagnostic(
        self, request_get, _load_rendered
    ) -> None:
        request_get.return_value = Mock(status_code=403)
        with self.assertRaisesRegex(RuntimeError, "主页HTTP 403.*重新登录知乎"):
            ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")

    @patch("src.platforms.zhihu.load_rendered_profile")
    @patch("src.platforms.zhihu.requests.get")
    def test_browser_latest_content_excludes_old_pinned_item_from_recent_window(
        self, request_get, load_rendered
    ) -> None:
        request_get.return_value = Mock(status_code=403)
        load_rendered.return_value = {
            "html": "", "activity_payloads": [], "content_links": [
                {"href": "https://zhuanlan.zhihu.com/p/1", "published": "2026-08-01 10:00"},
                {"href": "https://zhuanlan.zhihu.com/p/2", "published": "2026-10-10 12:00"},
                {"href": "https://zhuanlan.zhihu.com/p/3", "published": "2026-10-10 11:00"},
                {"href": "https://zhuanlan.zhihu.com/p/4", "published": "2026-10-10 10:00"},
            ],
        }
        from src.fetcher import select_recent_unique_articles
        items = select_recent_unique_articles(ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test"))
        self.assertEqual([item["article_id"] for item in items], ["article:2", "article:3", "article:4"])

    @patch("src.platforms.zhihu.load_rendered_profile")
    @patch("src.platforms.zhihu.requests.get")
    def test_rate_limit_stops_without_browser_retry(self, request_get, load_rendered) -> None:
        request_get.return_value = Mock(status_code=429)
        with self.assertRaisesRegex(RuntimeError, "请求过于频繁"):
            ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")
        load_rendered.assert_not_called()

    @patch("src.platforms.zhihu.get_zhihu_cookie", return_value="")
    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_requires_saved_login(self, request_get, _cookie) -> None:
        with self.assertRaisesRegex(RuntimeError, "尚未保存知乎登录状态"):
            ZhihuFetcher().fetch("org:demo")

        request_get.assert_not_called()

    def test_fetcher_reports_invalid_cookie_override(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "包含中文"):
            ZhihuFetcher().fetch("org:demo", cookie_override="这是错误内容")

    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_uses_cookie_when_explicitly_provided(self, request_get) -> None:
        response = Mock(status_code=200, text=SAMPLE_PAGE, url="https://www.zhihu.com/org/demo")
        response.raise_for_status.return_value = None
        request_get.return_value = response

        ZhihuFetcher().fetch("org:demo", cookie_override="z_c0=test")

        self.assertIn("Cookie", request_get.call_args.kwargs["headers"])

    @patch("src.platforms.zhihu.get_zhihu_cookie", return_value="z_c0=saved-token")
    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_uses_saved_cookie_by_default(self, request_get, _cookie) -> None:
        response = Mock(status_code=200, text=SAMPLE_PAGE, url="https://www.zhihu.com/org/demo")
        response.raise_for_status.return_value = None
        request_get.return_value = response

        ZhihuFetcher().fetch("org:demo")

        self.assertIn("Cookie", request_get.call_args.kwargs["headers"])
        self.assertIn(
            "z_c0=saved-token",
            request_get.call_args.kwargs["headers"]["Cookie"],
        )

    @patch("src.platforms.zhihu.get_zhihu_cookie", return_value="z_c0=saved-token")
    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_rejects_empty_cookie_override(
        self, request_get, _cookie
    ) -> None:
        response = Mock(status_code=200, text=SAMPLE_PAGE, url="https://www.zhihu.com/org/demo")
        response.raise_for_status.return_value = None
        request_get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "必须使用已登录"):
            ZhihuFetcher().fetch("org:demo", cookie_override="")

        request_get.assert_not_called()

    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_uses_org_profile_and_returns_parsed_items(self, request_get) -> None:
        response = Mock(status_code=200, text=SAMPLE_PAGE, url="https://www.zhihu.com/org/demo")
        response.raise_for_status.return_value = None
        request_get.return_value = response

        items = ZhihuFetcher().fetch("org:demo", cookie_override="z_c0=test")

        self.assertEqual(len(items), 2)
        self.assertEqual(request_get.call_args.args[0], "https://www.zhihu.com/org/demo")

    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_falls_back_to_activity_api(self, request_get) -> None:
        profile_response = Mock(
            status_code=200,
            text="<html><body><h1>测试用户</h1></body></html>",
            url="https://www.zhihu.com/people/demo",
        )
        profile_response.raise_for_status.return_value = None
        api_response = Mock(status_code=200, text="", url="https://www.zhihu.com/api/v4")
        api_response.raise_for_status.return_value = None
        api_response.json.return_value = SAMPLE_API
        request_get.side_effect = [profile_response, api_response]

        items = ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")

        self.assertEqual(
            [item["article_id"] for item in items],
            ["answer:901", "article:903"],
        )
        self.assertIn("/api/v4/members/demo/activities", request_get.call_args.args[0])

    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_falls_back_to_content_endpoints(self, request_get) -> None:
        def response(status_code=200, text="", payload=None, url="https://www.zhihu.com"):
            result = Mock(status_code=status_code, text=text, url=url, headers={})
            result.raise_for_status.return_value = None
            result.json.return_value = payload if payload is not None else {"data": []}
            return result

        def route(url, **_kwargs):
            if url == "https://www.zhihu.com/people/demo":
                return response(text="<html><h1>测试用户</h1></html>", url=url)
            if "/activities" in url:
                return response(payload={"data": []}, url=url)
            if "/answers" in url:
                return response(
                    payload={
                        "data": [{
                            "id": 901,
                            "question": {"id": 800, "title": "测试问题"},
                            "excerpt": "回答摘要",
                            "author": {"name": "测试用户"},
                        }]
                    },
                    url=url,
                )
            return response(payload={"data": []}, url=url)

        request_get.side_effect = route
        items = ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")

        self.assertEqual([item["article_id"] for item in items], ["answer:901"])
        self.assertTrue(any("/members/demo/answers" in call.args[0] for call in request_get.call_args_list))

    @patch("src.platforms.zhihu.requests.get")
    def test_activity_404_does_not_mark_existing_profile_as_missing(self, request_get) -> None:
        def response(status_code=200, text="", payload=None, url="https://www.zhihu.com"):
            result = Mock(status_code=status_code, text=text, url=url, headers={})
            result.raise_for_status.return_value = None
            result.json.return_value = payload if payload is not None else {"data": []}
            return result

        def route(url, **_kwargs):
            if url == "https://www.zhihu.com/people/demo":
                return response(text="<html><title>真实网名 - 知乎</title></html>", url=url)
            if "/activities" in url:
                return response(status_code=404, url=url)
            if "/answers" in url:
                return response(
                    payload={"data": [{
                        "id": 901,
                        "question": {"id": 800, "title": "测试问题"},
                        "excerpt": "回答正文",
                        "author": {"name": "真实网名"},
                    }]},
                    url=url,
                )
            return response(payload={"data": []}, url=url)

        request_get.side_effect = route
        items = ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")

        self.assertEqual(items[0]["author_name"], "真实网名")
        self.assertEqual(items[0]["summary"], "回答正文")

    @patch("src.platforms.zhihu.requests.get")
    def test_fetcher_reports_response_shape_when_no_creation_is_found(
        self, request_get
    ) -> None:
        profile_response = Mock(
            status_code=200,
            text="<html><body><h1>测试用户</h1></body></html>",
            url="https://www.zhihu.com/people/demo",
        )
        profile_response.raise_for_status.return_value = None
        api_response = Mock(
            status_code=200,
            text="",
            url="https://www.zhihu.com/api/v4",
            headers={"Content-Type": "application/json"},
        )
        api_response.raise_for_status.return_value = None
        api_response.json.return_value = {
            "data": [{"verb": "MEMBER_VOTEUP_ANSWER", "target": {"type": "answer"}}]
        }
        empty_content_response = Mock(
            status_code=200,
            text="",
            url="https://www.zhihu.com/api/v4",
            headers={"Content-Type": "application/json"},
        )
        empty_content_response.raise_for_status.return_value = None
        empty_content_response.json.return_value = {"data": []}
        request_get.side_effect = [
            profile_response,
            api_response,
            *([empty_content_response] * 5),
        ]

        with patch(
            "src.platforms.zhihu.load_rendered_profile",
            side_effect=RuntimeError("测试中不启动浏览器"),
        ):
            with self.assertRaisesRegex(RuntimeError, "活动1条.*MEMBER_VOTEUP_ANSWER"):
                ZhihuFetcher().fetch("people:demo", cookie_override="z_c0=test")


    def test_activity_api_extracts_pin_content_from_blocks(self) -> None:
        items = parse_activity_api({
            "data": [{
                "verb": "MEMBER_CREATE_PIN",
                "created_time": 1786579200,
                "actor": {"name": "测试用户"},
                "target": {
                    "id": 901,
                    "type": "pin",
                    "content": [
                        {"type": "text", "own_text": "这是想法正文。"},
                        {"type": "image", "url": "https://example.com/a.jpg"},
                    ],
                },
            }]
        })

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content_type"], "pin")
        self.assertEqual(items[0]["summary"], "这是想法正文。")

    @patch("src.platforms.zhihu.requests.get")
    def test_enrich_answer_excerpts_fills_empty_summaries(self, request_get) -> None:
        articles = [
            {"article_id": "answer:901", "content_type": "answer", "summary": ""},
            {"article_id": "answer:902", "content_type": "answer", "summary": "已有摘要"},
        ]
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [
                {"id": 901, "excerpt": "补齐的回答摘要"},
                {"id": 902, "excerpt": "不应覆盖"},
            ]
        }
        request_get.return_value = response

        ZhihuFetcher._enrich_answer_excerpts(articles, "members", "demo", {})

        self.assertEqual(articles[0]["summary"], "补齐的回答摘要")
        self.assertEqual(articles[1]["summary"], "已有摘要")

    @patch("src.platforms.zhihu.requests.get")
    def test_enrich_answer_excerpts_falls_back_to_answer_detail(self, request_get) -> None:
        articles = [
            {"article_id": "answer:901", "content_type": "answer", "summary": ""},
        ]
        list_response = Mock(status_code=200)
        list_response.raise_for_status.return_value = None
        list_response.json.return_value = {"data": [{"id": 901}]}
        detail_response = Mock(status_code=200)
        detail_response.raise_for_status.return_value = None
        detail_response.json.return_value = {"id": 901, "content": "<p>详情正文</p>"}
        request_get.side_effect = [list_response, detail_response]

        ZhihuFetcher._enrich_answer_excerpts(articles, "members", "demo", {})

        self.assertEqual(articles[0]["summary"], "详情正文")

    @patch("src.platforms.zhihu.get_zhihu_cookie", return_value="z_c0=test")
    @patch("src.platforms.zhihu.requests.get")
    def test_fetch_profile_name_reads_title(self, request_get, _cookie) -> None:
        response = Mock(
            status_code=200,
            text="<html><head><title>子夜极光 - 知乎</title></head><body></body></html>",
        )
        request_get.return_value = response

        name = ZhihuFetcher().fetch_profile_name("people:zi-ye-ji-guang-2015")

        self.assertEqual(name, "子夜极光")

    @patch("src.platforms.zhihu.get_zhihu_cookie", return_value="z_c0=test")
    @patch("src.platforms.zhihu.requests.get")
    def test_fetch_profile_name_falls_back_to_profile_api(self, request_get, _cookie) -> None:
        page_response = Mock(
            status_code=200,
            text="<html><head><title>知乎 - 有问题，就会有答案</title></head></html>",
        )
        profile_response = Mock(status_code=200)
        profile_response.json.return_value = {"name": "子夜极光"}
        request_get.side_effect = [page_response, profile_response]

        name = ZhihuFetcher().fetch_profile_name("people:zi-ye-ji-guang-2015")

        self.assertEqual(name, "子夜极光")


if __name__ == "__main__":
    unittest.main()
