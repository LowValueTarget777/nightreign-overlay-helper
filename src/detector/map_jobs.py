"""One map job at a time, using immutable screenshots and worker-owned caches."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock

from src.detector.map_detector import MapDetector


class MapJobs:
    def __init__(self, detector_factory=MapDetector):
        self._factory = detector_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='map-matching')
        self._future = None
        self._context = None
        self._param = None
        self._detector = None
        self._progress = None
        self._progress_lock = Lock()

    @property
    def busy(self):
        return self._future is not None

    def submit(self, param, context):
        if self.busy:
            return False
        # No capture API or QObject crosses into the matching worker.
        with self._progress_lock:
            self._progress = None
        param = replace(param, img=param.img.copy(), on_progress=self._publish_progress)
        self._context, self._param = context, param
        self._future = self._executor.submit(self._run, param)
        return True

    def _publish_progress(self, result):
        with self._progress_lock:
            # Only retain the newest snapshot, regardless of consumer speed.
            self._progress = result

    def _run(self, param):
        if self._detector is None:
            self._detector = self._factory()
        if param.do_match_pattern and param.earth_shifting is None:
            terrain = self._detector.detect(None, replace(param, do_match_pattern=False,
                                                          do_match_earth_shifting=True))
            if terrain.earth_shifting is None:
                return terrain
            param = replace(param, earth_shifting=terrain.earth_shifting)
        result = self._detector.detect(None, param)
        result.earth_shifting = param.earth_shifting
        return result

    def poll(self, context):
        if self._future is None:
            return None
        if not self._future.done():
            with self._progress_lock:
                progress, self._progress = self._progress, None
            if progress is not None and context == self._context:
                return self._param, progress
            return None
        future, self._future = self._future, None
        param, self._param = self._param, None
        with self._progress_lock:
            self._progress = None
        if context != self._context:
            # Observe exceptions but never publish an old session's result.
            future.exception()
            return None
        return param, future.result()

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._future = self._param = self._detector = None
        self._progress = None
