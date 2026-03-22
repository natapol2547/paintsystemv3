import bpy
from typing import Literal


class CollectionManager:
    def __init__(self, dataptr, propname, active_dataptr, active_propname):
        self._collection = getattr(dataptr, propname)
        self._active_index = getattr(active_dataptr, active_propname)
        if self._collection is None:
            raise ValueError(
                f"Collection {propname} is not found in {dataptr}")
        if self._active_index is None:
            raise ValueError(
                f"Active index {active_propname} is not found in {active_dataptr}")

    def add(self, properties: dict = None, position: Literal['TOP', 'BOTTOM', 'ABOVE', 'BELOW'] = 'BOTTOM'):
        item = self._collection.add()
        if properties:
            for key, value in properties.items():
                setattr(item, key, value)
        current_index = len(self._collection) - 1
        if position == 'TOP':
            self._collection.move(current_index, 0)
        elif position == 'BOTTOM':
            pass  # already at bottom
            # self.collection.move(current_index, len(self.collection) - 1)
        elif position == 'ABOVE':
            self._collection.move(current_index, self._active_index)
        elif position == 'BELOW':
            self._collection.move(current_index, self._active_index + 1)
        return item

    def remove(self, index: int):
        self._collection.remove(index)
        self._active_index = min(self._active_index, len(self._collection) - 1)

    def remove_active(self):
        self.remove(self._active_index)

    def move(self, index: int, new_index: int):
        self._collection.move(index, new_index)
        self._active_index = min(new_index, len(self._collection) - 1)

    def is_valid_move(self, direction: Literal['UP', 'DOWN']) -> bool:
        if direction == 'UP' and self._active_index > 0:
            return True
        elif direction == 'DOWN' and self._active_index < len(self._collection) - 1:
            return True
        return False

    def get(self, index: int):
        return self._collection[index]

    @property
    def collection(self):
        return self._collection

    @property
    def active_index(self):
        return self._active_index

    @property
    def active_item(self):
        return self._collection[self._active_index]
