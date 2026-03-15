"""Shared JSON file caching utilities for the Paint System addon."""

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_addon_root() -> str:
    """Get the addon root directory (one level up from this file's directory)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class JsonFileCache(ABC):
    """Abstract base class for JSON file caches with timestamp-based expiration.

    Subclasses must provide a ``filename`` class attribute (or property).
    Optionally override ``label`` for friendlier log messages.

    Example::

        class MyCache(JsonFileCache):
            filename = "my_cache.json"
            label = "my"

            def save_item(self, value: str) -> None:
                self.save({"value": value})

            def load_item(self, max_age: float) -> Optional[str]:
                cached = self.load(max_age)
                return cached.get("value") if cached is not None else None
    """

    @property
    @abstractmethod
    def filename(self) -> str:
        """Cache file name, stored in the addon root directory."""
        ...

    @property
    def label(self) -> str:
        """Human-readable label used in log messages."""
        return "cache"

    @property
    def path(self) -> str:
        return os.path.join(_get_addon_root(), self.filename)

    def save(self, data: Dict[str, Any]) -> None:
        """Save *data* to the cache file, stamped with the current time."""
        cache_data = {
            "timestamp": time.time(),
            "data": data,
        }
        try:
            with open(self.path, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving {self.label} cache: {e}")

    def load(self, max_age_seconds: float) -> Optional[Dict[str, Any]]:
        """Load cached data if the file exists and is younger than *max_age_seconds*.

        Returns:
            The cached data dict, or ``None`` if the cache is missing,
            expired, or corrupt.
        """
        if not os.path.exists(self.path):
            return None

        try:
            with open(self.path, 'r') as f:
                cache_data = json.load(f)

            timestamp = cache_data.get("timestamp", 0)
            if max_age_seconds > 0 and (time.time() - timestamp) > max_age_seconds:
                return None

            return cache_data.get("data")
        except Exception as e:
            logger.error(f"Error loading {self.label} cache: {e}")
            return None

    def reset(self) -> None:
        """Delete the cache file if it exists."""
        if os.path.exists(self.path):
            os.remove(self.path)
        logger.info(f"{self.label.capitalize()} cache reset")
