"""Windows process ownership shared by the application and updater."""
from pathlib import Path


class InstanceLock:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open('a+b')
        self.stream.seek(0)
        self.stream.write(b'0')
        self.stream.flush()
        self.stream.seek(0)
        try:
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except Exception:
            self.stream.close()
            raise RuntimeError('Application already running. Close it before starting another instance.')

    def close(self):
        self.stream.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
