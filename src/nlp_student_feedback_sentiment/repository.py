import json
from pathlib import Path


class RecordRepository:
    def __init__(self, path):
        self.path = Path(path)

    def _read_state(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"records": [], "review_events": []}
        if not isinstance(value, dict) or not isinstance(value.get("records"), list):
            raise ValueError("记录库格式无效")
        value.setdefault("review_events", [])
        if not isinstance(value["review_events"], list):
            raise ValueError("复核记录格式无效")
        return value

    def read(self):
        return self._read_state()["records"]

    def read_review_events(self):
        return self._read_state()["review_events"]

    def replace(self, records, review_events=None):
        state = self._read_state()
        state["records"] = records
        if review_events is not None:
            state["review_events"] = review_events
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
