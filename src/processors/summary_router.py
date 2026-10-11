"""根据博主背景和单条视频内容选择内部简报策略。"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from ..security.cache_paths import cache_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_PATH = PROJECT_ROOT / "config" / "source_summary_profiles.json"
ROUTE_DIR = PROJECT_ROOT / "output" / "summary_routes"
ROUTER_VERSION = "5"


STRATEGIES = {
    "knowledge_lesson": {
        "name": "知识课程",
        "instructions": "连贯概括课程讲解的概念、推导关系、例题方法和成立条件；优先保留定义、关键步骤与易错点，不把教学类比当作正式定义。",
        "content_focus": "按课程的讲解顺序串联概念、推导、例题方法和结论。",
        "fact_mode": "strict",
    },
    "tutorial_guide": {
        "name": "教程方法",
        "instructions": "围绕视频要解决的问题，按实际演示顺序概括操作过程、前置条件、适用范围和视频提到的失败情况；不要把一次演示成功写成对所有设备都有效。",
        "content_focus": "用“先、随后、最后”等自然衔接词复述操作过程，不写成步骤清单。",
        "fact_mode": "strict",
    },
    "product_review": {
        "name": "产品测评",
        "instructions": "概括测评对象、测试过程、实际观察和博主结论；区分测试数据、厂商宣称和主观感受，保留版本、价格、测试条件与视频中呈现的限制。",
        "content_focus": "连贯说明测了什么、如何测试、观察到什么以及博主得出了什么结论。",
        "fact_mode": "strict",
    },
    "technology_explainer": {
        "name": "科技科普",
        "instructions": "按视频论述顺序概括技术原理、发展关系、关键参数和视频给出的结论；区分历史事实、行业判断和简化类比。",
        "content_focus": "把原理、发展过程、关键参数和结论组织成前后连贯的说明。",
        "fact_mode": "strict",
    },
    "news_analysis": {
        "name": "资讯分析",
        "instructions": "按时间与因果整理事件，严格区分已确认信息、转述、预测和个人观点；缺少原始来源时明确待核实。",
        "content_focus": "按时间和因果关系复述事件、现有信息以及视频作者的判断。",
        "fact_mode": "strict",
    },
    "opinion_commentary": {
        "name": "观点评论",
        "instructions": "准确概括视频作者的中心观点、论据、例子和最终结论；用归属表达标明这是作者观点，不由模型另行评价、反驳或补充观点。",
        "content_focus": "按照作者提出问题、展开论证和得出结论的顺序进行复述。",
        "fact_mode": "balanced",
    },
    "fan_creation": {
        "name": "二创作品",
        "instructions": "连贯介绍作品情节或内容、原作关联、创作形式、角色和实际呈现的视听特点；准确区分二创、实机素材和官方作品，控制剧情剧透。",
        "content_focus": "按作品自身的叙事顺序概括情节、角色互动和创作形式。",
        "fact_mode": "source_identity",
    },
    "gameplay_entertainment": {
        "name": "游戏内容",
        "instructions": "简要交代游戏、玩法目标和结果，突出操作、高光、阵容或趣味过程；不要把娱乐性实机内容写成攻略定论。",
        "content_focus": "按游戏过程串联目标、主要操作、关键变化和最终结果。",
        "fact_mode": "light",
    },
    "entertainment": {
        "name": "娱乐内容",
        "instructions": "只概括人物、情境、过程和观看氛围，保留笑点与反转，不做沉重说教，不完整复述导致失去观看乐趣。",
        "content_focus": "用简短连贯的叙述交代人物、情境、过程和结果，避免过度剧透。",
        "fact_mode": "light",
    },
    "educational_story": {
        "name": "教育短片",
        "instructions": "按剧情发展概括人物、冲突、转折和结局，并准确转述创作者想表达的道理；不得把戏剧化案例或创作者观点包装成普遍定律。",
        "content_focus": "先连贯概括剧情，再说明短片借此表达的观点。",
        "fact_mode": "balanced",
    },
    "vlog_experience": {
        "name": "记录体验",
        "instructions": "整理经历、场景、个人感受和重要见闻；区分个人体验与可普遍推广的结论。",
        "content_focus": "按实际经历顺序复述去了哪里、做了什么、遇到什么以及博主的感受。",
        "fact_mode": "balanced",
    },
    "music_performance": {
        "name": "音乐表演",
        "instructions": "介绍作品、表演或剪辑形式、参与者与视听特色；避免虚构曲目来源、官方身份和制作人员。",
        "content_focus": "连贯概括表演内容、参与者、呈现形式和画面中实际出现的变化。",
        "fact_mode": "source_identity",
    },
    "commercial_recommendation": {
        "name": "工具推荐",
        "instructions": "概括视频推荐的工具、演示功能、操作过程和使用场景；未经独立验证的安全、性能、免费和效果只能写成博主声称，不由模型给出使用建议。",
        "content_focus": "按视频演示顺序说明工具是什么、如何使用以及视频声称能达到什么效果。",
        "fact_mode": "strict",
    },
    "general": {
        "name": "综合内容",
        "instructions": "根据材料准确、连贯地概括视频主题、主要过程、关键信息和最终结论；事实不足时使用保守表述，不加入模型评价。",
        "content_focus": "按照视频本身的叙事或论述顺序概括内容。",
        "fact_mode": "balanced",
    },
}


KEYWORDS = {
    "knowledge_lesson": ["必修", "课程", "习题", "例题", "定理", "证明", "量词", "集合", "知识点"],
    "tutorial_guide": ["教程", "怎么", "方法", "步骤", "设置", "安装", "解决", "清理", "优化", "技巧"],
    "product_review": ["测评", "实测", "开箱", "体验", "对比", "性能", "价格", "主机", "硬盘", "值不值", "值得买吗"],
    "technology_explainer": ["原理", "发展史", "技术", "为什么", "揭秘", "深挖", "显示器", "系统应用"],
    "news_analysis": ["发布", "官宣", "新规", "政策", "事件", "最新消息", "宣布", "更新"],
    "opinion_commentary": ["为什么现在", "误解", "现象", "原因", "观点", "文化", "成年人"],
    "fan_creation": ["二创", "同人", "原片", "描改", "填词", "剪辑", "后期", "动画化", "动画op", "非ai动画", "二次元", "动漫", "mad", "amv", "鬼畜", "虚拟主播", "vtuber"],
    "gameplay_entertainment": ["游戏", "无伤", "全息", "高光", "阵容", "配队", "通关", "斯普拉遁", "涂击队", "实机"],
    "entertainment": ["搞笑", "整活", "挑战", "离谱", "笑死", "名场面"],
    "educational_story": ["幸福", "人生", "成年人", "恶意", "过来人", "道理", "人情世故"],
    "vlog_experience": ["现场", "旅行", "日常", "记录", "探店", "体验一天"],
    "music_performance": ["演唱会", "完整版", "bgm", "翻唱", "歌曲", "舞蹈", "音乐"],
    "commercial_recommendation": ["分享", "推荐", "工具箱", "软件", "助手", "下载", "无需安装", "值得试试"],
}


class SummaryRouter:
    """不向用户展示分类，只为AI生成适合当前视频的内部规划。"""

    @staticmethod
    def _profiles() -> dict:
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            return data.get("profiles", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _article_id(article: dict) -> str:
        current_id = str(
            article.get("article_id")
            or article.get("video_id")
            or article.get("id")
            or ""
        )
        if current_id:
            return current_id
        match = re.search(r"(BV[0-9A-Za-z]+)", str(article.get("link", "")))
        return match.group(1) if match else "unknown"

    @staticmethod
    def _score(
        metadata_text: str,
        transcript_text: str,
        default_strategy: str,
    ) -> dict[str, int]:
        metadata_text = metadata_text.lower()
        transcript_text = transcript_text.lower()
        scores = {key: 0 for key in STRATEGIES}
        if default_strategy in scores:
            scores[default_strategy] += 3
        for strategy, keywords in KEYWORDS.items():
            for keyword in keywords:
                keyword = keyword.lower()
                if keyword in metadata_text:
                    scores[strategy] += 3
                if keyword in transcript_text:
                    scores[strategy] += 1
        return scores

    def route(self, article: dict, transcript: str) -> dict:
        platform = str(article.get("platform", ""))
        uid = str(article.get("source_uid", ""))
        profile = self._profiles().get(f"{platform}:{uid}", {})
        title = str(article.get("title", ""))
        description = str(article.get("description") or article.get("summary") or "")
        transcript_sample = "\n".join([transcript[:1800], transcript[-900:]])
        default_strategy = str(profile.get("default_strategy", "general"))
        source_hash = hashlib.sha256(
            "\n".join(
                [
                    ROUTER_VERSION,
                    platform,
                    uid,
                    title,
                    description,
                    transcript,
                    json.dumps(profile, ensure_ascii=False, sort_keys=True),
                ]
            ).encode("utf-8")
        ).hexdigest()
        current_id = self._article_id(article)
        cache_path = cache_file(ROUTE_DIR, current_id)
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("source_hash") == source_hash:
                cached.setdefault("cache_path", str(cache_path))
                return cached
        except (OSError, ValueError, json.JSONDecodeError):
            pass

        scores = self._score(
            "\n".join([title, description]),
            transcript_sample,
            default_strategy,
        )
        ranked = sorted(scores, key=lambda key: scores[key], reverse=True)
        primary = ranked[0] if scores[ranked[0]] > 0 else "general"
        secondary = [
            key
            for key in ranked[1:]
            if scores[key] >= 4 and scores[key] >= scores[primary] - 2
        ][:2]
        strategy = STRATEGIES[primary]
        secondary_focus = [STRATEGIES[key]["instructions"] for key in secondary]
        result = {
            "version": ROUTER_VERSION,
            "article_id": current_id,
            "primary_strategy": primary,
            "secondary_strategies": secondary,
            "strategy_name": strategy["name"],
            "fact_mode": strategy["fact_mode"],
            "instructions": strategy["instructions"],
            "secondary_focus": secondary_focus,
            "content_focus": strategy["content_focus"],
            "creator_context": profile.get("creator_context", ""),
            "description": description[:12000],
            "source": str(article.get("source", "")),
            "requires_visual": primary in {"fan_creation", "gameplay_entertainment", "music_performance", "entertainment"},
            "guardrails": profile.get("guardrails", []),
            "scores": {key: value for key, value in scores.items() if value > 0},
            "source_hash": source_hash,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cache_path": str(cache_path),
        }
        ROUTE_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
        return result
