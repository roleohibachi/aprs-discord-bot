#This overloaded dict will only keep the most recent ten items, 
#and automatically pop the oldest one.
#relies on python3 ordered dicts
#It's necessary because clients often don't receive their ACKs (such is radio)
from collections import Mapping
class RingDict(dict):
    def __init__(self, size: int = 10, other=None, **kwargs):
        super().__init__()
        self.size = size
        self.update(other, **kwargs)

    def __setitem__(self, key, value):
        if len(self)>=self.size: #gotta make room for the new value
            self.pop(next(iter(self))) #pop the oldest
        super().__setitem__(key, value)

    def update(self, other=None, **kwargs):
        if other is not None:
            for k, v in other.items() if isinstance(other, Mapping) else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v
