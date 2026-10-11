import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from src.processors import character_recognition as cr, character_models as cm, media_knowledge as mk
from src.processors.video_frame_extractor import VisualFrame


class CharacterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for module, attr, value in ((cr, "ROOT", self.root), (cr, "CONFIG_PATH", self.root / "config.json"),
            (mk, "REFERENCE_DIR", self.root / "images"), (mk, "KNOWLEDGE_PATH", self.root / "knowledge.json")):
            patcher = patch.object(module, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "images").mkdir()
        for name, color in (("a.jpg", "red"), ("b.jpg", "blue")):
            Image.new("RGB", (100, 200), color).save(self.root / "images" / name)
        self.entries = [{"id": k, "name": n, "work": w, "kind": "character", "text": "外观资料", "source_url": "https://example.org/" + k,
                         "reference_images": [k + ".jpg"], "variant": "默认"} for k, n, w in (("a", "角色甲", "作品甲"), ("b", "角色乙", "作品乙"))]
        mk.save_entries(self.entries)
        self.client = Mock(api_url="https://example.org/api", vision_model="gpt-5-test")
        self.recognizer = cr.CharacterRecognizer(self.client, mk.MediaKnowledge())
        self.frames = [VisualFrame(1, str(self.root / "images" / "a.jpg")), VisualFrame(2, str(self.root / "images" / "b.jpg"))]

    def crops(self, second=False, role="depicted", distance=.1):
        crops = [{"query_id": "q0", "subject_id": "a", "frame_id": 0, "timestamp": 1, "role": "depicted", "path": self.frames[0].path,
                  "candidates": [{"entry_id": "a", "name": "角色甲", "work": "作品甲", "distance": distance}]}]
        if second:
            crops.append({**crops[0], "query_id": "q1", "frame_id": 1, "role": role, "candidates": [{"entry_id": "b", "name": "角色乙", "work": "作品乙", "distance": .1}]})
        return crops

    def match(self, query="q0", entry="a", **kwargs):
        return {"query_id": query, "entry_id": entry, "confidence": "high", "matched_traits": ["头饰", "衣领纹样", "腰间饰物"], "conflicts": [], **kwargs}

    def test_reference_metadata_and_atomic_append(self):
        self.assertEqual(mk.MediaKnowledge().entries[0]["variant"], "默认")
        extra = {**self.entries[0], "id": "c", "name": "新角色"}
        mk.append_entries([extra])
        self.assertEqual(len(mk.MediaKnowledge().entries), 3)
        with self.assertRaises(ValueError):
            mk.append_entries([extra])

    def test_wd_suffix_match_requires_correct_work(self):
        entry = {"name": "胡桃", "aliases": ["Hu Tao"], "work": "原神"}
        self.assertTrue(cr.tag_matches_entry("hu_tao_(genshin_impact)", entry))
        self.assertFalse(cr.tag_matches_entry("hu_tao_(honkai:_star_rail)", entry))

    def test_crop_rejects_unknown_roles_bad_coordinates_and_frame_ids(self):
        good = {"frame_id": 0, "bbox": [100, 100, 900, 900], "role": "depicted"}
        bad = [{**good, "frame_id": -1}, {**good, "bbox": [0, 0, float("nan"), 900]}, {**good, "bbox": [900, 0, 100, 100]}, {**good, "role": "guess"}]
        crops = cr.validated_crops({"subjects": [*bad, good]}, self.frames, self.root, 10)
        self.assertEqual(len(crops), 1)
        with Image.open(crops[0]["path"]) as image:
            self.assertEqual(image.size, (80, 160))

    def test_only_supplied_candidate_ids_can_name_character(self):
        result = cr.CharacterRecognizer.resolve(self.crops(), {"matches": [self.match(entry="invented-name")]})
        self.assertIsNone(result[0]["identity"])
        self.assertEqual(result[0]["status"], "unknown")

    def test_high_visual_match_is_verified_with_traits(self):
        row = cr.CharacterRecognizer.resolve(self.crops(), {"matches": [self.match()]})[0]
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["identity"]["name"], "角色甲")
        self.assertEqual(row["identity"]["variant"], "具体形象版本未确认")

    def test_known_identity_does_not_require_known_variant(self):
        crops = self.crops()
        crops[0]["candidates"][0]["variant"] = "新皮肤"
        row = cr.CharacterRecognizer.resolve(crops, {"matches": [self.match(variant_confirmed=False)]})[0]
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["identity"]["reference_variant"], "新皮肤")
        self.assertEqual(row["identity"]["variant"], "具体形象版本未确认")

    def test_conflict_or_weak_feature_match_stays_candidate(self):
        for override in ({"conflicts": ["头饰不符"]}, {"confidence": "medium"}, {"matched_traits": ["黑发"]}):
            row = cr.CharacterRecognizer.resolve(self.crops(), {"matches": [self.match(**override)]})[0]
            self.assertIsNone(row["identity"])
        row = cr.CharacterRecognizer.resolve(self.crops(distance=.8), {"matches": [self.match()]})[0]
        self.assertIsNone(row["identity"])

    def test_cross_frame_conflicting_names_are_both_rejected(self):
        result = cr.CharacterRecognizer.resolve(self.crops(True), {"matches": [self.match(), self.match("q1", "b")]})
        self.assertTrue(all(r["status"] == "conflicting" and r["identity"] is None for r in result))

    def test_original_template_is_separate_from_depicted_identity(self):
        result = cr.CharacterRecognizer.resolve(self.crops(True, role="template"), {"matches": [self.match(), self.match("q1", "b")]})
        self.assertEqual([r["status"] for r in result], ["verified", "verified"])
        self.assertEqual([r["role"] for r in result], ["depicted", "template"])

    def test_multiple_frames_can_support_stylized_identity(self):
        crops = self.crops(distance=.5)
        crops.append({**crops[0], "query_id": "q1", "frame_id": 1})
        result = cr.CharacterRecognizer.resolve(crops, {"matches": [self.match(), self.match("q1", "a")]})
        self.assertTrue(all(r["status"] == "verified" for r in result))

    def test_medium_match_needs_multiple_frames_and_separation_from_other_candidates(self):
        crops = self.crops(distance=.1)
        crops[0]["candidates"].append({"entry_id": "b", "name": "角色乙", "work": "作品乙", "distance": .3})
        crops.append({**crops[0], "query_id": "q1", "frame_id": 1})
        matches = {"matches": [self.match(confidence="medium"), self.match("q1", "a", confidence="medium")]}
        result = cr.CharacterRecognizer.resolve(crops, matches)
        self.assertTrue(all(r["status"] == "verified" for r in result))
        crops[0]["candidates"][1]["distance"] = .11
        result = cr.CharacterRecognizer.resolve(crops, matches)
        self.assertTrue(all(r["identity"] is None for r in result))

    def test_duplicate_verification_is_rejected(self):
        row = cr.CharacterRecognizer.resolve(self.crops(), {"matches": [self.match(), self.match()]})[0]
        self.assertIsNone(row["identity"])

    def test_image_retrieval_does_not_require_character_name_in_query(self):
        crops = self.crops()
        gallery = [{"entry": entry, "reference_image": entry["id"] + ".jpg"} for entry in mk.MediaKnowledge().entries]
        features = [np.zeros(768, np.float32), np.ones(768, np.float32)]
        with patch.object(cm, "ccip_features", return_value=np.zeros((1, 768), np.float32)), patch.object(cm, "ccip_distances", return_value=np.array([[0, .4, .1], [.4, 0, .3], [.1, .3, 0]])), patch.object(cm, "model_status", return_value={"wd_ready": False}):
            self.recognizer.retrieve(crops, gallery, features)
        self.assertEqual(crops[0]["candidates"][0]["entry_id"], "b")

    def test_gallery_image_changes_invalidate_identity_cache(self):
        before = self.recognizer.fingerprint()
        Image.new("RGB", (100, 100), "green").save(self.root / "images" / "a.jpg")
        self.assertNotEqual(before, self.recognizer.fingerprint())

    def test_variants_of_same_name_do_not_fill_all_retrieval_slots(self):
        crops = self.crops()
        entries = mk.MediaKnowledge().entries
        gallery = [{"entry": entries[0], "reference_image": "a.jpg"},
                   {"entry": {**entries[0], "id": "a2", "variant": "另一版本"}, "reference_image": "a.jpg"},
                   {"entry": entries[1], "reference_image": "b.jpg"}]
        with patch.object(cm, "ccip_features", return_value=np.zeros((1, 768), np.float32)), patch.object(cm, "ccip_distances", return_value=np.array([[0, .1, .1, .2]])), patch.object(cm, "model_status", return_value={"wd_ready": False}):
            self.recognizer.retrieve(crops, gallery, [np.zeros(768)] * 3)
        self.assertEqual([c["name"] for c in crops[0]["candidates"]], ["角色甲", "角色乙"])

    def test_failed_verification_keeps_explicit_warning(self):
        with patch.object(cm, "model_status", return_value={"ccip_ready": True}), patch.object(self.recognizer, "gallery_features", return_value=([{}], [np.zeros(768)])), patch.object(self.recognizer, "request", side_effect=RuntimeError("HTTP 504")):
            result = self.recognizer.analyze(self.frames, self.root)
        self.assertEqual(result["status"], "failed")
        self.assertIn("504", result["warnings"][0])

    def test_successful_identity_result_is_cached_and_new_frame_invalidates_it(self):
        with patch.object(cm, "model_status", return_value={"ccip_ready": True}), patch.object(self.recognizer, "gallery_features", return_value=([{}], [np.zeros(768)])), patch.object(self.recognizer, "request", return_value={"subjects": []}) as request:
            first = self.recognizer.analyze(self.frames, self.root)
            second = self.recognizer.analyze(self.frames, self.root)
            self.assertEqual(first["source_hash"], second["source_hash"])
            self.assertEqual(request.call_count, 1)
            Image.new("RGB", (100, 100), "orange").save(self.frames[0].path)
            third = self.recognizer.analyze(self.frames, self.root)
            self.assertNotEqual(third["source_hash"], second["source_hash"])
            self.assertEqual(request.call_count, 2)

    def test_resume_rejects_wrong_range_start_and_servers_ignoring_range(self):
        response = Mock(status_code=206, is_redirect=False, headers={"Content-Range": "bytes 0-100/200"})
        with patch.object(cm.requests, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "续传响应无效"):
                cm._download_range("https://huggingface.co/model", self.root / "partial", 101, require_range=True)
        response.status_code = 200
        with patch.object(cm.requests, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "不支持"):
                cm._download_range("https://huggingface.co/model", self.root / "partial", 101, require_range=True)

    def test_missing_models_skip_network_requests(self):
        with patch.object(cm, "model_status", return_value={"ccip_ready": False}):
            self.assertEqual(self.recognizer.analyze(self.frames, self.root)["status"], "unavailable")
        self.client._call_api.assert_not_called()

    def test_local_model_errors_do_not_destroy_scene_analysis(self):
        with patch.object(cm, "model_status", return_value={"ccip_ready": True}), patch.object(self.recognizer, "gallery_features", side_effect=Exception("ONNX failure")):
            result = self.recognizer.analyze(self.frames, self.root)
        self.assertEqual(result["status"], "failed")
        self.assertIn("本地模型", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
