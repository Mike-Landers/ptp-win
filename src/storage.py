from __future__ import annotations

import json
import os
from pathlib import Path


APP_TITLE = "DropLink"


def default_store_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / APP_TITLE / "shares.json"


class DownloadStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_store_path()
        import threading
        self.lock = threading.RLock()

    def records(self) -> list[dict]:
        with self.lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return []
        shares = payload.get("shares", {}) if isinstance(payload, dict) else {}
        if not isinstance(shares, dict):
            return []
        incomplete = []
        for record in shares.values():
            files = record.get("files", [])
            if files and all(not item.get("selected", True) or item.get("completed", False) for item in files):
                continue
            incomplete.append(record)
        if len(incomplete) != len(shares):
            with self.lock:
                if incomplete:
                    self.path.write_text(json.dumps({"version": 1, "shares": {item["link"]: item for item in incomplete}}, indent=2), encoding="utf-8")
                elif self.path.exists():
                    self.path.unlink()
        return incomplete

    def get(self, link: str) -> dict | None:
        return next((record for record in self.records() if record.get("link") == link), None)

    def save(self, record: dict) -> None:
        with self.lock:
            records = {item.get("link"): item for item in self.records() if item.get("link")}
            records[record["link"]] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"version": 1, "shares": records}, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)

    def remove(self, link: str) -> None:
        with self.lock:
            records = {item.get("link"): item for item in self.records() if item.get("link") and item.get("link") != link}
            if records:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"version": 1, "shares": records}, indent=2), encoding="utf-8")
            elif self.path.exists():
                self.path.unlink()
