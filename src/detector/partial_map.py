"""Positive-only landmark evidence for maps with undiscovered icons hidden."""
import cv2
import numpy as np


class LandmarkEvidence:
    def __init__(self):
        self.confirmed = {}
        self.pending = {}
        self._rank_key = None
        self._ranked = None
        self._rank_patterns = None

    def clear(self):
        self.confirmed.clear()
        self.pending.clear()
        self._rank_key = None
        self._ranked = None
        self._rank_patterns = None

    def observe(self, observations):
        pending = {}
        for pos, kind in observations.items():
            old_kind, count = self.pending.get(pos, (None, 0))
            count = count + 1 if old_kind == kind else 1
            pending[pos] = (kind, count)
            if count >= 2:
                self.confirmed[pos] = kind
        # Occlusion is not evidence that a previously seen landmark vanished.
        self.pending = pending

    def rank(self, patterns, icon_key, icon_aliases=None):
        icon_aliases = icon_aliases or {}
        key = (id(patterns), icon_key, tuple(sorted(self.confirmed.items())),
               tuple(sorted(icon_aliases.items())))
        if key == self._rank_key:
            return self._ranked
        ranked = []
        for pattern in patterns:
            matches = 0
            for pos, kind in self.confirmed.items():
                construct = pattern.pos_constructions.get(pos)
                if construct is not None:
                    expected = icon_key(construct.type)
                    if icon_aliases.get(expected, expected) == kind:
                        matches += 1
            ranked.append((pattern, matches, len(self.confirmed) - matches))
        self._rank_key = key
        self._rank_patterns = patterns  # Keep the identity in the cache key alive.
        self._ranked = sorted(ranked, key=lambda row: (row[2], -row[1], row[0].id))
        return self._ranked


class PartialMapInference:
    def __init__(self, map_info, categories):
        self.evidence = LandmarkEvidence()
        self.positions = sorted(set().union(*map_info.all_poi_pos.values()))
        self.templates = []
        self.icon_aliases = {}
        visual_keys = {}
        for key, category in sorted(categories.items()):
            if key == 0:
                continue
            rgba = np.array(category.base_image)
            # Normal and DLC categories can share the exact same artwork.
            # They are one visual observation, not competing runner-up matches.
            visual = (rgba.shape, rgba.tobytes())
            canonical = visual_keys.setdefault(visual, key)
            self.icon_aliases[key] = canonical
            if canonical != key:
                continue
            alpha = rgba[:, :, 3]
            ys, xs = np.where(alpha > 220)
            if len(xs) < 15:
                continue
            left, top, right, bottom = xs.min(), ys.min(), xs.max()+1, ys.max()+1
            gray = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
            template = gray[top:bottom, left:right].copy()
            mask = (alpha[top:bottom, left:right] > 220).astype(np.uint8) * 255
            offset = (left-rgba.shape[1]//2, top-rgba.shape[0]//2)
            self.templates.append((key, template, mask, offset))

    def scan(self, image):
        """Match opaque icon pixels only, never the missing terrain background.

        Thresholds are conservative initial values pending event screenshots.
        Full-map scale is required; no SIFT fit to hidden terrain is attempted.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        observations = {}
        for pos in self.positions:
            scores = []
            for key, template, mask, (dx, dy) in self.templates:
                h, w = template.shape
                x, y = pos[0]+dx, pos[1]+dy
                if x < 4 or y < 4 or x+w+4 > gray.shape[1] or y+h+4 > gray.shape[0]:
                    continue
                patch = gray[y-4:y+h+4, x-4:x+w+4]
                if patch.std() < 12:
                    continue
                result = cv2.matchTemplate(patch, template, cv2.TM_SQDIFF_NORMED, mask=mask)
                result = np.where(np.isfinite(result), result, np.inf)
                iy, ix = np.unravel_index(result.argmin(), result.shape)
                score = float(result[iy, ix])
                scores.append((score, key, template, mask, patch[iy:iy+h, ix:ix+w]))
            scores.sort(key=lambda row: (row[0], row[1]))
            if scores and scores[0][0] < 0.12:
                if len(scores) == 1 or scores[1][0] - scores[0][0] >= 0.035:
                    _, key, template, mask, matched = scores[0]
                    # Similar terrain brightness alone is not an icon. Require
                    # the opaque pixels' light/dark pattern to agree as well.
                    correlation = float(cv2.matchTemplate(
                        matched, template, cv2.TM_CCOEFF_NORMED, mask=mask)[0, 0])
                    if np.isfinite(correlation) and correlation >= 0.65:
                        observations[pos] = key
        self.evidence.observe(observations)
        return len(self.evidence.confirmed)
