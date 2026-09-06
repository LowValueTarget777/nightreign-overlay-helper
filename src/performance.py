"""Small byte-bounded caches for immutable image processing results."""
from collections import OrderedDict


class ByteCache:
    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.bytes = 0
        self._items = OrderedDict()

    def get(self, key, factory):
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key][0]
        value = factory()
        size = value.nbytes if hasattr(value, 'nbytes') else value.width * value.height * len(value.getbands())
        if size <= self.max_bytes:
            while self._items and self.bytes + size > self.max_bytes:
                _, (_, old_size) = self._items.popitem(last=False)
                self.bytes -= old_size
            self._items[key] = (value, size)
            self.bytes += size
        return value

    def clear(self):
        self._items.clear()
        self.bytes = 0
