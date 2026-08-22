"""In-process pixel buffers keyed by shm_id.

Common perception reads frames from here. Capture backends write PNG/BGRA
bytes. FrameHandle never carries the bytes themselves.
"""


class FrameStore:
    def __init__(self) -> None:
        self._frames: dict[str, bytes] = {}

    def put(self, shm_id: str, data: bytes) -> None:
        self._frames[shm_id] = data

    def get(self, shm_id: str) -> bytes:
        return self._frames[shm_id]

    def __contains__(self, shm_id: str) -> bool:
        return shm_id in self._frames
