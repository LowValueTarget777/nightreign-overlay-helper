"""Offline, deterministic benchmarks; no game, Steam or capture session required.

Run from the repository root:
  .venv/Scripts/python -m scripts.benchmark_performance --output build/performance/baseline.json
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ['APPDATA'] = str(args.output.parent.resolve() / 'appdata')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    import cv2
    import numpy as np
    from PIL import Image
    from src.detector.map_detector import MapDetector, MapPatternMatchResult
    from src.detector.utils import grab_region
    from src.logger import set_log_level
    from logging import ERROR

    set_log_level(ERROR)
    cv2.setRNGSeed(42)
    report = {'kind': 'offline synthetic, not game FPS', 'opencv': cv2.__version__}

    def measure(name, fn, repeats=5):
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            value = fn()
            times.append((time.perf_counter() - start) * 1000)
        report[name] = {'first_ms': times[0], 'median_ms': statistics.median(times[1:]),
                        'samples_ms': times}
        return value

    frame = Image.fromarray(np.random.default_rng(42).integers(0, 256, (1440, 2560, 3), dtype=np.uint8))
    frame.info['screen_origin'] = (0, 0)
    engine = SimpleNamespace(grab_fullscreen=lambda: frame)
    regions = [(100, 100, 150, 50), (200, 200, 200, 30), (1454, 228, 990, 990), (100, 500, 900, 30)]
    crops = measure('four_regions', lambda: [grab_region(engine, r) for r in regions], 21)
    report['crop_hashes'] = [hashlib.sha256(im.tobytes()).hexdigest() for im in crops]

    detector = MapDetector()
    patterns = detector.info.patterns[:5]
    matches = [MapPatternMatchResult(p, p.nightlord, 0, 0) for p in patterns]
    images = measure('five_overlays', lambda: [detector._draw_overlay_image(m, (990, 990), i)
                                              for i, m in enumerate(matches)])
    report['overlay_hashes'] = [hashlib.sha256(im.tobytes()).hexdigest() for im in images]

    # Use the same immutable background and exact icon composites each run.
    from src.detector.map_detector import open_cv2_image, MAG_BG_FOR_POI_MATCH_INDEX_MAP
    pattern = patterns[0]
    bg = open_cv2_image(f'maps_poi_match/{MAG_BG_FOR_POI_MATCH_INDEX_MAP[pattern.earth_shifting]}.jpg', (750, 750))
    canvas = Image.fromarray(bg).convert('RGBA')
    positions = sorted(detector.info.all_poi_pos[(pattern.earth_shifting, pattern.nightlord)])
    for pos in positions:
        construct = pattern.pos_constructions.get(pos)
        if construct and construct.type in detector.all_poi_images:
            canvas.alpha_composite(detector.all_poi_images[construct.type], (pos[0]-22, pos[1]-22))
    pixels = np.array(canvas.convert('RGB'))
    values = measure('building_scan', lambda: [detector._match_poi(pixels, bg, pos, pattern.earth_shifting,
                                                                  pattern.nightlord) for pos in positions])
    report['building_types'] = [int(row[0]) for row in values]
    report['building_scores'] = [float(row[1]) if np.isfinite(row[1]) else None for row in values]
    report['positions'] = len(positions)
    report['cache_bytes'] = {name: getattr(detector, name).bytes for name in
        ('_resources', '_poi_templates', '_overlay_layers') if hasattr(detector, name)}
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if isinstance(v, dict)}, indent=2))


if __name__ == '__main__':
    main()
