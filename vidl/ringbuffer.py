from collections import deque


class RingBuffer:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")

        self._buffer = deque(maxlen=capacity)

    def append(self, item) -> None:
        self._buffer.append(item)

    def __len__(self) -> int:
        return len(self._buffer)

    def __iter__(self):
        return iter(self._buffer)

    def __repr__(self) -> str:
        return f"RingBuffer({list(self._buffer)!r})"
