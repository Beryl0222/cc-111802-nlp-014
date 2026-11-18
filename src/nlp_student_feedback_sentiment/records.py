from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class TextRecord:
    record_id: str
    source: str
    revision: int
    observed_at: str
    text: str
    context: str = ""
    course_id: str = ""
    class_id: str = ""
    authorized_reviewers: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise TypeError("文本记录必须是对象")
        reviewers = value.get("authorized_reviewers", ())
        if isinstance(reviewers, str):
            reviewers = (reviewers,)
        if not isinstance(reviewers, (list, tuple)):
            raise ValueError("授权复核人必须是列表")
        record = cls(
            record_id=str(value.get("record_id", "")).strip(),
            source=str(value.get("source", "")).strip(),
            revision=int(value.get("revision", 0)),
            observed_at=str(value.get("observed_at", "")).strip(),
            text=str(value.get("text", "")),
            context=str(value.get("context", "")).strip(),
            course_id=str(value.get("course_id", "")).strip(),
            class_id=str(value.get("class_id", "")).strip(),
            authorized_reviewers=tuple(
                str(reviewer).strip() for reviewer in reviewers if str(reviewer).strip()
            ),
        )
        if not record.record_id or not record.source or record.revision < 1 or not record.text.strip():
            raise ValueError("文本记录缺少身份、来源、有效修订号或正文")
        datetime.fromisoformat(record.observed_at.replace("Z", "+00:00"))
        return record

    def to_dict(self):
        value = asdict(self)
        value["authorized_reviewers"] = list(self.authorized_reviewers)
        return value
