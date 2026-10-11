import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.ai_connection import TEST_IMAGE
from src.fetcher import enrich_article
from src.processors import deepseek_summarizer
from src.processors.whisper_transcriber import NoSpeechError


class VisualOnlySummaryTests(unittest.TestCase):
    def test_image_evidence_can_be_summarized_without_inventing_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frame.png'
            path.write_bytes(base64.b64decode(TEST_IMAGE.split(',')[1]))
            frame = Mock(path=str(path), timestamp=0)
            client = deepseek_summarizer.DeepSeekSummarizer()
            client.vision_enabled = True
            with patch.object(deepseek_summarizer, 'SUMMARY_DIR', Path(directory)), \
                 patch.object(client, '_call_api', return_value={'choices': [{'message': {'content': '仅依据画面，展示了一个蓝色区域。'}}]}) as call:
                result = client.summarize('BV123', 'title', '', frames=[frame])
            self.assertEqual(result.analysis_mode, 'visual')
            prompt = call.call_args.args[0][1]['content'][0]['text']
            self.assertIn('不能推断配乐、音效、对白', prompt)
            self.assertIn('标题只能作为背景', prompt)

    def test_no_input_is_rejected_before_model_call(self):
        client = deepseek_summarizer.DeepSeekSummarizer()
        with patch.object(client, '_call_api') as call:
            with self.assertRaisesRegex(ValueError, '没有可用字幕或关键帧'):
                client.summarize('BV123', 'title', '')
            call.assert_not_called()

    def test_no_speech_falls_back_to_frames_in_enrichment(self):
        article = {'platform': 'bilibili', 'article_id': 'BV1TcNc6FEun', 'title': 'video', 'link': 'https://www.bilibili.com/video/BV1TcNc6FEun'}
        frame_result = Mock(frames=[Mock()], duration=10, scanned_frame_count=5, scan_interval=2,
                            selection_strategy='general', manifest_path='cache')
        summary = Mock(model='vision-model', content='画面摘要', cache_path='cache', analysis_mode='visual',
                       frame_count=1, summary_strategy='general', router_version='1')
        with patch('src.fetcher.BilibiliSubtitleFetcher.fetch', return_value=None), \
             patch('src.fetcher.WhisperTranscriber.transcribe', side_effect=NoSpeechError('no speech')), \
             patch('src.fetcher.VideoFrameExtractor.extract', return_value=frame_result), \
             patch('src.fetcher.SummaryRouter.route', return_value={}), \
             patch.object(deepseek_summarizer.DeepSeekSummarizer, '_load_provider_config', return_value={'vision_enabled': True}), \
             patch.object(deepseek_summarizer.DeepSeekSummarizer, 'is_configured', return_value=True), \
             patch.object(deepseek_summarizer.DeepSeekSummarizer, 'summarize', return_value=summary) as call:
            enrich_article(article)
        self.assertEqual(article['ai_status'], 'available')
        self.assertEqual(article['transcript_status'], 'no_speech')
        self.assertEqual(call.call_args.kwargs['transcript'], '')
        self.assertEqual(call.call_args.kwargs['bvid'], article['article_id'])

    def test_visual_model_failure_without_speech_does_not_invent_text_fallback(self):
        article = {'platform': 'bilibili', 'article_id': 'BV1TcNc6FEun', 'title': 'video', 'link': 'https://www.bilibili.com/video/BV1TcNc6FEun'}
        frames = Mock(frames=[Mock()], duration=10, scanned_frame_count=5, scan_interval=2,
                      selection_strategy='general', manifest_path='cache')
        with patch('src.fetcher.BilibiliSubtitleFetcher.fetch', return_value=None), \
             patch('src.fetcher.WhisperTranscriber.transcribe', side_effect=NoSpeechError('no speech')), \
             patch('src.fetcher.VideoFrameExtractor.extract', return_value=frames), \
             patch('src.fetcher.SummaryRouter.route', return_value={}), \
             patch.object(deepseek_summarizer.DeepSeekSummarizer, '_load_provider_config', return_value={'vision_enabled': True}), \
             patch.object(deepseek_summarizer.DeepSeekSummarizer, 'is_configured', return_value=True), \
             patch.object(deepseek_summarizer.DeepSeekSummarizer, 'summarize', side_effect=RuntimeError('model failed')) as call:
            with self.assertRaisesRegex(RuntimeError, '视觉模型调用失败'):
                enrich_article(article)
            self.assertEqual(call.call_count, 1)
