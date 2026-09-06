"""Regressions for shared normal/DLC landmark artwork.

Run: python -m unittest scripts.test_partial_icon_aliases
"""
import os
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['APPDATA'] = str(Path('build/partial-debug/test-appdata').resolve())

import numpy as np
from PIL import Image
from src.detector.map_detector import MapDetector, MapDetectParam, get_poi_key, open_cv2_image
from src.detector.partial_map import PartialMapInference, LandmarkEvidence


class SharedIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = MapDetector()

    def test_every_real_category_survives_shared_artwork(self):
        info = NS(all_poi_pos={(0, 0): {(100, 100)}})
        for key, category in self.detector.poi_cate_info.items():
            if not key:
                continue
            with self.subTest(category=key):
                inference = PartialMapInference(info, self.detector.poi_cate_info)
                canvas = Image.new('RGBA', (200, 200), (40, 40, 40, 255))
                icon = category.base_image
                canvas.alpha_composite(icon, (100-icon.width//2, 100-icon.height//2))
                frame = np.array(canvas.convert('RGB'))
                self.assertEqual(inference.scan(frame), 0)
                self.assertEqual(inference.scan(frame), 1)
                self.assertEqual(inference.evidence.confirmed[(100, 100)],
                                 inference.icon_aliases[key])

    def test_aliases_keep_both_normal_and_dlc_candidates(self):
        inference = PartialMapInference(self.detector.info, self.detector.poi_cate_info)
        for normal, dlc in [(34, 525), (38, 524), (40, 511), (41, 510)]:
            self.assertEqual(inference.icon_aliases[normal], inference.icon_aliases[dlc])
        evidence = LandmarkEvidence()
        evidence.confirmed = {(10, 10): 38, (20, 20): 40}
        def pattern(identifier, a, b):
            return NS(id=identifier, pos_constructions={
                (10, 10): NS(type=a), (20, 20): NS(type=b)})
        patterns = [pattern(1, 38000, 40000), pattern(2, 52400, 51100),
                    pattern(3, 30000, 34000)]
        ranked = evidence.rank(patterns, get_poi_key, inference.icon_aliases)
        self.assertEqual([(r[0].id, r[1], r[2]) for r in ranked],
                         [(1, 2, 0), (2, 2, 0), (3, 0, 2)])
        self.assertIs(ranked, evidence.rank(patterns, get_poi_key, inference.icon_aliases))
        self.assertNotEqual(ranked[1][1:], evidence.rank(patterns, get_poi_key)[1][1:])

    def test_featureless_maps_never_add_landmarks(self):
        inference = PartialMapInference(self.detector.info, self.detector.poi_cate_info)
        for brightness in (0, 40, 128, 255):
            frame = np.full((750, 750, 3), brightness, dtype=np.uint8)
            self.assertEqual(inference.scan(frame), 0)
            self.assertEqual(inference.scan(frame), 0)
        self.assertEqual(inference.evidence.pending, {})

    def test_terrain_without_building_icons_adds_no_landmarks(self):
        for terrain in (0, 4):
            with self.subTest(terrain=terrain):
                inference = PartialMapInference(self.detector.info, self.detector.poi_cate_info)
                frame = open_cv2_image(f'maps_poi_match/{terrain}.jpg', (750, 750))
                self.assertEqual(inference.scan(frame), 0)
                self.assertEqual(inference.scan(frame), 0)
                self.assertEqual(inference.evidence.pending, {})

    def test_shared_icons_on_terrain_with_offset_and_jpeg(self):
        pos = (239, 415)
        info = NS(all_poi_pos={(0, 0): {pos}})
        for key in (34, 38, 40, 41, 525, 524, 511, 510):
            with self.subTest(category=key):
                inference = PartialMapInference(info, self.detector.poi_cate_info)
                canvas = Image.fromarray(open_cv2_image('maps_poi_match/0.jpg', (750, 750))).convert('RGBA')
                icon = self.detector.poi_cate_info[key].base_image
                canvas.alpha_composite(icon, (pos[0]-icon.width//2+3, pos[1]-icon.height//2-2))
                encoded = BytesIO()
                canvas.convert('RGB').save(encoded, format='JPEG', quality=90)
                encoded.seek(0)
                frame = np.array(Image.open(encoded).convert('RGB'))
                self.assertEqual(inference.scan(frame), 0)
                self.assertEqual(inference.scan(frame), 1)
                self.assertEqual(inference.evidence.confirmed[pos], inference.icon_aliases[key])

    def test_two_shared_icons_generate_overlay_candidates(self):
        detector = MapDetector()
        inference = PartialMapInference(detector.info, detector.poi_cate_info)
        pattern = detector.info.patterns[0]
        canvas = Image.new('RGBA', (750, 750), (40, 40, 40, 255))
        selected = []
        for pos, construct in pattern.pos_constructions.items():
            key = get_poi_key(construct.type)
            if pos in inference.positions and key in (34, 38, 40, 41):
                icon = detector.poi_cate_info[key].base_image
                canvas.alpha_composite(icon, (pos[0]-icon.width//2, pos[1]-icon.height//2))
                selected.append(pos)
            if len(selected) == 2:
                break
        self.assertEqual(len(selected), 2)
        param = MapDetectParam(map_region=(0, 0, 750, 750), infer_partial=True,
                               img=np.array(canvas.convert('RGB')), return_pattern_topk=2)
        self.assertFalse(detector.detect(None, param).overlay_images)
        result = detector.detect(None, param)
        self.assertEqual(len(result.overlay_images), 2)
        self.assertEqual(len(detector.partial_inference.evidence.confirmed), 2)
        self.assertIn('并列候选', result.inference_message)


if __name__ == '__main__':
    unittest.main()
