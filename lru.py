"""A dict kept in least recently used order, for the addon's in-memory caches.

Selection masks, texel maps, surface keys and view pass targets each
keep a bounded number of entries and drop the least recently used one
first. `LRUCache` holds that bookkeeping once. What each cache counts
against its budget, and what it does with a dropped entry, stays with
the cache.
"""


class LRUCache(dict):
    """A dict that iterates from the least to the most recently used key.

    A dict keeps its keys in insertion order. `touch` moves a key to the
    end, so the first key is always the least recently used. Reading
    with `get` or `[]` does not count as a use, and neither does storing
    a new value under a key that is already there.
    """

    def touch(self, key):
        """The value stored for *key*, which becomes the most recently used. None when absent."""
        value = self.pop(key, None)
        if value is not None:
            self[key] = value
        return value

    def trim(self, budget: int, cost=None, keep=()) -> list:
        """Drop the least recently used entries until their total cost is at most *budget*.

        *cost* maps a value to what it counts against the budget, such as
        its video memory. Without it every entry counts 1, so *budget* is
        an entry count. Keys in *keep* are never dropped, even if that
        leaves the total over budget. Returns the dropped values, least
        recently used first, for the caller to free.
        """
        total = len(self) if cost is None else sum(cost(value) for value in self.values())
        dropped = []
        for key in list(self):
            if total <= budget:
                break
            if key in keep:
                continue
            value = self.pop(key)
            total -= 1 if cost is None else cost(value)
            dropped.append(value)
        return dropped
