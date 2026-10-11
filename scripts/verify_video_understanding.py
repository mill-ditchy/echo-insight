"""Reanalyse selected local history items and save a reviewable before/after record."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fetcher import enrich_article
from src.processors.deepseek_summarizer import DeepSeekSummarizer
from src.processors.video_analysis_config import atomic_json
from src.update_history import update_ai_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bvids", nargs="+")
    parser.add_argument("--vision-model", default="")
    parser.add_argument("--publish", action="store_true", help="Update visible history after successful analysis")
    args = parser.parse_args()
    history = json.loads((ROOT / "data" / "update_history.json").read_text(encoding="utf-8"))["items"]
    records = []
    # An override tests an available model at the saved provider; it never changes saved credentials.
    original_init = DeepSeekSummarizer.__init__
    def initialize(client):
        original_init(client)
        if args.vision_model:
            client.vision_model = args.vision_model
    DeepSeekSummarizer.__init__ = initialize
    output = ROOT / "output" / "research" / "video-understanding-2026-10-10" / f"实测对照-{args.bvids[0]}.json"
    for bvid in args.bvids:
        original = next((item for item in history if item.get("article_id") == bvid), None)
        if not original:
            raise ValueError("历史记录中没有指定视频")
        article = dict(original)
        record = {"bvid": bvid, "title": article["title"], "before": {k: original.get(k) for k in ("ai_summary", "ai_analysis_mode", "ai_frame_count")}}
        start = time.perf_counter()
        print("ANALYSE", bvid, article["title"], flush=True)
        try:
            enrich_article(article, progress_callback=lambda stage: print("STAGE", bvid, stage, flush=True))
            record["after"] = {k: article.get(k) for k in ("ai_summary", "ai_model", "ai_analysis_mode", "ai_frame_count", "ai_segment_count", "ai_completeness", "ai_analysis_warnings", "ai_evidence_file")}
            if args.publish:
                update_ai_result("bilibili", bvid, "available", ai_summary=article["ai_summary"], ai_model=article["ai_model"],
                    analysis_mode=article["ai_analysis_mode"], frame_count=article["ai_frame_count"], visual_error=article.get("visual_error", ""),
                    summary_strategy=article.get("summary_strategy", ""), router_version=article.get("summary_router_version", ""), analysis_details=article)
            print("RESULT", json.dumps(record["after"], ensure_ascii=False), flush=True)
        except Exception as exc:
            record["error"] = str(exc)
            print("FAILED", bvid, str(exc), flush=True)
        record["elapsed_seconds"] = round(time.perf_counter() - start, 1)
        records.append(record)
        atomic_json(output, {"records": records})
    if any("error" in record for record in records):
        sys.exit(1)


if __name__ == "__main__":
    main()
