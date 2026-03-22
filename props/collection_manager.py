import bpy
from typing import Literal


class CollectionManager:
    def __init__(self, dataptr, propname, active_dataptr, active_propname):
        self._dataptr = dataptr
        self._propname = propname
        self._active_dataptr = active_dataptr
        self._active_propname = active_propname
        if self.collection is None:
            raise ValueError(
                f"Collection {propname} is not found in {dataptr}")
        if self.active_index is None:
            raise ValueError(
                f"Active index {active_propname} is not found in {active_dataptr}")

    def add(self, properties: dict = None, position: Literal['TOP', 'BOTTOM', 'ABOVE', 'BELOW'] = 'BELOW'):
        item = self.collection.add()
        if properties:
            for key, value in properties.items():
                setattr(item, key, value)
        current_index = len(self.collection) - 1
        if position == 'TOP':
            self.move(current_index, 0)
        elif position == 'BOTTOM':
            pass  # already at bottom
            # self.move(current_index, len(self.collection) - 1)s
        elif position == 'ABOVE':
            self.move(current_index, self.active_index)
        elif position == 'BELOW':
            self.move(current_index, self.active_index + 1)
        return item

    def remove(self, index: int):
        self.collection.remove(index)
        self.active_index = min(self.active_index, len(self.collection) - 1)

    def remove_active(self):
        self.remove(self.active_index)

    def move(self, index: int, new_index: int):
        self.collection.move(index, new_index)
        self.active_index = min(new_index, len(self.collection) - 1)

    def is_valid_move(self, direction: Literal['UP', 'DOWN']) -> bool:
        if direction == 'UP' and self.active_index > 0:
            return True
        elif direction == 'DOWN' and self.active_index < len(self.collection) - 1:
            return True
        return False

    def get(self, index: int):
        return self.collection[index]

    @property
    def collection(self):
        return getattr(self._dataptr, self._propname)

    @property
    def active_index(self):
        return getattr(self._active_dataptr, self._active_propname)

    @active_index.setter
    def active_index(self, value: int):
        setattr(self._active_dataptr, self._active_propname,
                min(value, len(self.collection) - 1))

    @property
    def active_item(self):
        return self.collection[self.active_index]
