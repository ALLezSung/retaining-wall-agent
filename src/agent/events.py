"""Small synchronous event contract, independent of any UI."""
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Event:
    name: str
    data: dict = field(default_factory=dict)


EventSink = Callable[[Event], None]
