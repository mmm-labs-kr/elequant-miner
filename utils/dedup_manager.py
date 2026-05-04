import hashlib
import json
from pathlib import Path


class DedupManager:
    """Tracks tried alpha codes by hash to prevent duplicate simulations.

    Share data/shared_tried.json with teammates so everyone mines different strategies.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._hashes: set[str] = set()
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                self._hashes = set(data.get('hashes', []))
            except Exception:
                pass

    def _save(self):
        self.path.write_text(
            json.dumps({'hashes': sorted(self._hashes)}, indent=2),
            encoding='utf-8'
        )

    @staticmethod
    def _hash(code: str) -> str:
        return hashlib.sha256(code.strip().encode()).hexdigest()

    def is_duplicate(self, code: str) -> bool:
        return self._hash(code) in self._hashes

    def add(self, code: str):
        h = self._hash(code)
        if h not in self._hashes:
            self._hashes.add(h)
            self._save()

    @property
    def count(self) -> int:
        return len(self._hashes)
