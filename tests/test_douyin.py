import json
import tempfile
import unittest
from pathlib import Path
from http.cookies import SimpleCookie
from unittest.mock import Mock, patch
from types import SimpleNamespace

from fastapi.testclient import TestClient
import webapp
from src import source_manager, update_history, state_manager
from src.fetcher import needs_ai_summary, enrich_article
from src.platforms.douyin import normalize_uid, parse_post, media_url, post_title, DouyinFetcher
from src.platforms import douyin_browser, douyin_status, douyin_media
from src.accounts import douyin_account
from src.processors import douyin_summary, xiaohongshu_summary
from src.processors.deepseek_summarizer import DeepSeekSummarizer

UID = 'MS4wLjABAAAAaCcBHb3Rhc4zxF8YkBOfHfLh6k-IWEK2l3Ne9xOXPnQ'
IDENT = '7684908933646945588'
PHOTO = 'https://p3.douyinpic.com/test.jpg'
VIDEO = 'https://v11.douyinvod.com/test.mp4'


def raw(**overrides):
    return dict(aweme_id=IDENT, aweme_type=68, desc='配文：实验在低温条件下有效。', create_time=1728000000,
                author={'sec_uid': UID, 'nickname': '测试作者'}, images=[{'url_list': [PHOTO]}], **overrides)


def article():
    return {**parse_post(raw(), UID), 'platform': 'douyin', 'source_uid': UID, 'source': '测试作者'}


class DouyinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(douyin_status, 'DATA_PATH', self.root/'status.json'),
                        patch.object(source_manager, 'CONFIG_PATH', self.root/'sources.json'),
                        patch.object(update_history, 'DATA_PATH', self.root/'history.json'),
                        patch.object(update_history, 'SUMMARY_DIR', self.root/'summaries'),
                        patch.object(state_manager, 'STATE_PATH', self.root/'state.json')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def test_uid_ignores_share_query_but_rejects_non_profiles(self):
        self.assertEqual(normalize_uid(f'https://www.douyin.com/user/{UID}?vid={IDENT}&from_tab_name=main'), UID)
        for value in ['../private', '昵称', f'https://douyin.com.evil.test/user/{UID}', f'https://www.douyin.com/video/{IDENT}', f'https://user@www.douyin.com/user/{UID}']:
            with self.subTest(value=value), self.assertRaises(ValueError): normalize_uid(value)

    def test_album_preserves_sequence_body_and_count(self):
        data=raw();data['images']=[{'url_list':[PHOTO]}, {'url_list':[PHOTO+'?n=2']}]
        item=parse_post(data,UID)
        self.assertEqual(item['images'],[PHOTO,PHOTO+'?n=2'])
        self.assertEqual(item['image_count'],2)
        self.assertIn('低温',item['summary'])
        self.assertEqual(item['content_type'],'image_note')
        self.assertEqual(item['article_id'],'dy_'+IDENT)
        self.assertEqual(post_title('主题\n详细配文 #话题'),'主题')
        self.assertEqual(post_title('#话题'),'抖音作品')
        self.assertLessEqual(len(post_title('长配文'*100)),81)

    def test_video_is_not_misidentified_as_album(self):
        data=raw();data.update(aweme_type=0,images=None,video={'duration':90000,'play_addr':{'url_list':[VIDEO]}})
        item=parse_post(data,UID)
        self.assertEqual(item['content_type'],'video');self.assertEqual(item['duration'],90)
        self.assertEqual(item['media_urls'],[VIDEO]);self.assertEqual(item['detail_status'],'available')

    def test_rejects_recommendations_private_ads_and_unsupported(self):
        for changes in [{'author':{'sec_uid':'someone-else'}},{'status':{'is_private':True}},{'is_ads':True},{'is_share_post':True},{'aweme_type':101}]:
            data=raw();data.update(changes)
            self.assertIsNone(parse_post(data,UID))

    def test_unsafe_image_does_not_claim_full_read(self):
        data=raw();data['images'].append({'url_list':['https://127.0.0.1/private']})
        item=parse_post(data,UID)
        self.assertEqual(item['detail_status'],'preview_only');self.assertEqual(item['image_count'],2)
        for url in ['file:///secret', 'https://douyinvod.com.evil.test/v', 'https://user@v11.douyinvod.com/v', 'https://v11.douyinvod.com:444/v']:
            self.assertEqual(media_url(url),'')

    @patch('src.platforms.douyin.load_secret',return_value='')
    @patch('src.platforms.douyin_browser.load_profile')
    def test_baseline_sort_and_incremental_do_not_import_old_pinned_posts(self,load,_secret):
        rows=[]
        for i in range(6):
            item=raw();item.update(aweme_id=str(int(IDENT)+i),create_time=1728000000+i,is_top=i==0);rows.append(item)
        load.return_value={'posts':rows,'profile_name':'测试作者'}
        fetcher=DouyinFetcher();baseline=fetcher.fetch(UID)
        self.assertEqual([a['note_id'] for a in baseline],[str(int(IDENT)+i) for i in [5,4,3]])
        self.assertEqual(fetcher.fetch_incremental(UID,[a['article_id'] for a in baseline]),[])
        # An older manually included example must not turn intervening history
        # into new unread posts.
        self.assertEqual(fetcher.fetch_incremental(UID,[baseline[0]['article_id'],'dy_'+str(int(IDENT)+1)]),[])
        latest=raw();latest.update(aweme_id=str(int(IDENT)+6),create_time=1728000006)
        rows.append(latest)
        self.assertEqual(len(fetcher.fetch_incremental(UID,[a['article_id'] for a in baseline])),1)

    def test_challenge_stops_before_reading_data(self):
        with patch.object(douyin_browser.edge,'_evaluate',return_value={'url':'https://www.douyin.com/','text':'请完成安全验证'}):
            with self.assertRaises(douyin_browser.DouyinAccessError): douyin_browser.check_access(Mock())

    def test_response_listener_only_accepts_official_post_paths(self):
        def event(url,rid):return {'method':'Network.responseReceived','params':{'requestId':rid,'response':{'url':url,'status':200}}}
        client=Mock(events=[event('https://www.douyin.com/aweme/v1/web/aweme/post/?signed=secret','good'),event('https://evil.test/aweme/v1/web/aweme/post/','bad'),event('https://www.douyin.com/aweme/v1/web/comment/list/','comments')])
        client.call.return_value={'body':json.dumps({'status_code':0,'aweme_list':[raw()]})}
        handled=set();rows=douyin_browser._responses(client,handled)
        self.assertEqual(len(rows),1);self.assertEqual(handled,{'good'});self.assertEqual(client.call.call_count,1)

    def test_http_rejection_is_not_no_new_content(self):
        client=Mock(events=[{'method':'Network.responseReceived','params':{'requestId':'1','response':{'url':'https://www.douyin.com/aweme/v1/web/aweme/post/','status':429}}}])
        with self.assertRaises(douyin_browser.DouyinAccessError):douyin_browser._responses(client,set())

    def test_login_requires_session_and_filters_cookie_domain(self):
        with self.assertRaises(ValueError):douyin_account.normalize_cookie('guest=123')
        good=SimpleCookie();good['sessionid']='local-session';good['sessionid']['domain']='.douyin.com'
        evil=SimpleCookie();evil['foreign']='never-save';evil['foreign']['domain']='douyin.com.evil.test'
        with patch.object(douyin_account,'save_cookie') as save,patch.object(douyin_account,'get_account_status',return_value={}):
            douyin_account.save_webview_login([good,evil]);self.assertEqual(save.call_args.args[0],'sessionid=local-session')

    def test_history_keeps_media_metadata_and_success_after_failure(self):
        item=article();item['media_urls']=[VIDEO];item['duration']=3
        update_history.record_baseline_preview([item])
        update_history.update_ai_result('douyin',item['article_id'],'available',ai_summary='已有简报')
        update_history.update_ai_result('douyin',item['article_id'],'error',error='模拟网络失败')
        saved=update_history.list_updates(platform='douyin')[0]
        self.assertEqual(saved['ai_summary'],'已有简报');self.assertEqual(saved['last_attempt_status'],'error')
        self.assertEqual(saved['media_urls'],[VIDEO]);self.assertEqual(saved['duration'],3)

    def test_both_supported_types_route_to_douyin_analyzer(self):
        with patch.object(douyin_summary,'enrich_post') as process:
            for kind in ['image_note','video']:
                item=article();item['content_type']=kind
                self.assertTrue(needs_ai_summary(item));enrich_article(item)
            self.assertEqual(process.call_count,2)
        self.assertFalse(needs_ai_summary({'platform':'douyin','content_type':'live'}))

    def test_image_summary_combines_caption_and_ocr_and_vision(self):
        from PIL import Image
        photo=self.root/'sample.jpg';Image.new('RGB',(50,50)).save(photo)
        model=Mock(model='text',vision_model='vision',vision_enabled=True)
        model.is_configured.return_value=True;model._source_hash.return_value='hash';model._load_cache.return_value=None
        model.normalize_summary_text.side_effect=lambda s:s
        responses=[{'choices':[{'message':{'content':'指标提升20%[图片1]'}}]}, {'choices':[{'message':{'content':'低温下指标提升20%[正文][图片1]'}}]}]
        with patch.object(xiaohongshu_summary,'DeepSeekSummarizer',return_value=model),patch.object(xiaohongshu_summary,'EVIDENCE_DIR',self.root/'evidence'),patch.object(xiaohongshu_summary,'download_image',return_value=photo),patch.object(xiaohongshu_summary,'read_frames',return_value=([{'text':'提升20%'}],'')),patch.object(xiaohongshu_summary,'_request',side_effect=responses) as request:
            item=article();douyin_summary.enrich_post(item)
            prompt=request.call_args.args[1][1]['content']
            self.assertIn('低温条件',prompt);self.assertIn('提升20%',prompt);self.assertIn('不逐张描述',prompt)
            self.assertEqual(item['ai_note_reading_mode'],'full');self.assertEqual(item['summary_strategy'],'douyin_image_note')
            self.assertEqual(item['ai_frame_count'],1)

    def test_image_failure_preserves_old_summary(self):
        model=Mock(model='text',vision_enabled=True);model.is_configured.return_value=True
        with patch.object(xiaohongshu_summary,'DeepSeekSummarizer',return_value=model),patch.object(xiaohongshu_summary,'EVIDENCE_DIR',self.root/'evidence'),patch.object(xiaohongshu_summary,'download_image',side_effect=RuntimeError('failure')):
            item=article();item['ai_summary']='旧简报'
            with self.assertRaises(RuntimeError):douyin_summary.enrich_post(item)
            self.assertEqual(item['ai_summary'],'旧简报')

    def test_source_create_supports_douyin_and_groups_both_types(self):
        image=article();video={**article(),'article_id':'dy_7691255735242580726','content_type':'video','content_type_label':'视频'}
        fetcher=Mock();fetcher.read_profile.return_value={'articles':[video,image],'profile_name':'测试作者'}
        with patch.object(webapp,'get_fetcher',return_value=fetcher),patch.object(webapp,'_start_baseline_processing',return_value=True):
            client=TestClient(webapp.app)
            result=client.post('/api/sources',headers={'X-Cyber-Request':'1'},json={'platform':'douyin','uid':'https://www.douyin.com/user/'+UID})
            self.assertEqual(result.status_code,200,result.text)
            detail=client.get('/api/sources/douyin/'+UID,headers={'X-Cyber-Request':'1'})
            self.assertEqual({t['key'] for t in detail.json()['source']['content_types']},{'video','image_note'})
            self.assertEqual(len(detail.json()['videos']),2)
        with patch.object(webapp,'_start_visual_reanalysis',return_value=True) as start:
            response=client.post('/api/videos/douyin/'+image['article_id']+'/reanalyze',headers={'X-Cyber-Request':'1'},json={'reading_mode':'full'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(start.call_args.args[0]['requested_note_reading_mode'],'full')

    def test_local_media_cache_ids_cannot_escape_directory(self):
        from src.processors.whisper_transcriber import WhisperTranscriber
        from src.processors.video_frame_extractor import VideoFrameExtractor
        for processor,method in [(WhisperTranscriber(),'transcribe'),(VideoFrameExtractor(),'extract')]:
            with self.assertRaises(ValueError):getattr(processor,method)('',local_path=self.root/'a.mp4',media_id='../escape')

    def test_partial_video_retry_does_not_overwrite_previous_summary_cache(self):
        from src.processors import deepseek_summarizer as module
        model=DeepSeekSummarizer.__new__(DeepSeekSummarizer)
        model.vision_enabled=True;model.vision_model='vision';model.model='text'
        model._source_hash=Mock(return_value='hash');model._build_messages=Mock(return_value=[])
        model._save_cache=Mock();model._call_api=Mock()
        frame=SimpleNamespace(path='unused',timestamp=1)
        segment=SimpleNamespace(start=0,end=2,frames=[frame],clip_path='')
        analyzer=Mock();analyzer.fingerprint.return_value='fingerprint';analyzer.analyze.return_value={'failures':[{'start':0,'end':2}]}
        with patch.object(module,'VideoUnderstanding',return_value=analyzer),patch.object(module,'load_settings',return_value={'enabled':True}):
            with self.assertRaisesRegex(RuntimeError,'保留上次'):
                model.summarize('dy_'+IDENT,'视频','语音',frames=[frame],segments=[segment],summary_plan={'preserve_on_partial_failure':True},use_cache=False)
        model._save_cache.assert_not_called();model._call_api.assert_not_called()

    def test_optional_reference_warning_does_not_mark_video_coverage_partial(self):
        media=Mock();media.streams.audio=[];media.duration=0
        media.__enter__=Mock(return_value=media);media.__exit__=Mock(return_value=False)
        model=Mock(vision_enabled=True);model.is_configured.return_value=True
        result=SimpleNamespace(content='视频内容总结',model='vision',analysis_mode='visual',frame_count=8,
            segment_count=1,completeness='sampled',input_mode='images',evidence_file='',
            analysis_warnings=['百科未找到相关资料'],summary_strategy='general',router_version='5')
        model.summarize.return_value=result
        for completeness in ['sampled','partial']:
            result.completeness=completeness
            video=self.root/'video.mp4';video.write_bytes(b'placeholder')
            item={**article(),'content_type':'video','media_urls':[VIDEO]}
            with patch.object(douyin_summary,'DeepSeekSummarizer',return_value=model),patch.object(douyin_summary,'download_video',return_value=video),patch('av.open',return_value=media),patch.object(douyin_summary,'SummaryRouter') as router,patch.object(douyin_summary,'VideoFrameExtractor') as extractor:
                router.return_value.route.return_value={}
                extractor.return_value.extract.return_value=SimpleNamespace(frames=[],segments=[])
                douyin_summary.enrich_post(item)
            self.assertEqual(item['ai_completeness'],completeness)
            self.assertEqual(item['ai_analysis_warnings'],['百科未找到相关资料'])

    def test_truncated_video_summary_is_not_saved(self):
        model=DeepSeekSummarizer.__new__(DeepSeekSummarizer)
        model.vision_enabled=False;model.model='text';model._source_hash=Mock(return_value='hash')
        model._build_messages=Mock(return_value=[]);model._save_cache=Mock()
        model._call_api=Mock(return_value={'choices':[{'finish_reason':'length','message':{'content':'未完成'}}]})
        with self.assertRaisesRegex(RuntimeError,'截断'):
            model.summarize('dy_'+IDENT,'视频','语音',use_cache=False)
        model._save_cache.assert_not_called()

    def test_media_redirect_and_download_do_not_send_session(self):
        with self.assertRaises(ValueError):douyin_media.ImageRedirect(douyin_media.MEDIA_DOMAINS).redirect_request(Mock(),None,302,'',{},'https://127.0.0.1/private')
        response=Mock();response.headers={};response.geturl.return_value=VIDEO;response.read.side_effect=[b'video',b''];response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        media=Mock();media.streams.video=[Mock()];media.__enter__=Mock(return_value=media);media.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        with patch.object(douyin_media,'CACHE_DIR',self.root/'media'),patch.object(douyin_media,'build_opener',return_value=opener),patch('av.open',return_value=media):
            result=douyin_media.download_video({'article_id':'dy_'+IDENT,'media_urls':[VIDEO]})
            self.assertEqual(result.read_bytes(),b'video')
            self.assertNotIn('Cookie',dict(opener.open.call_args.args[0].header_items()))

    def test_frame_reader_uses_actual_stream_when_audio_is_first(self):
        import av
        import numpy as np
        from fractions import Fraction
        from src.processors.video_frame_extractor import VideoFrameExtractor
        path=self.root/'audio-first.mp4'
        with av.open(str(path),'w') as output:
            audio=output.add_stream('aac',rate=8000)
            video=output.add_stream('mpeg4',rate=2);video.width=64;video.height=64;video.pix_fmt='yuv420p'
            sample=av.AudioFrame.from_ndarray(np.zeros((1,8000),dtype=np.float32),format='flt',layout='mono');sample.sample_rate=8000;sample.pts=0;sample.time_base=Fraction(1,8000)
            for packet in audio.encode(sample):output.mux(packet)
            for i in range(4):
                frame=av.VideoFrame.from_ndarray(np.full((64,64,3),i*50,dtype=np.uint8),format='rgb24');frame.pts=i
                for packet in video.encode(frame):output.mux(packet)
            for stream in [audio,video]:
                for packet in stream.encode():output.mux(packet)
        with av.open(str(path)) as media:
            self.assertEqual(media.streams.video[0].index,1)
            image=VideoFrameExtractor._read_frame(media,media.streams.video[0],.5)
            self.assertEqual(image.size,(64,64))


if __name__ == '__main__':unittest.main()
