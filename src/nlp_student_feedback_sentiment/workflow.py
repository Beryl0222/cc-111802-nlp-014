import hashlib
from datetime import datetime, timezone

from .records import TextRecord
from .text import annotate, normalize_text


DECISIONS = ("confirm", "dismiss", "needs_context")
DECISION_LABELS = {
    "confirm": "确认风险",
    "dismiss": "确认非风险（特殊语境）",
    "needs_context": "需补充上下文，待判断",
}


def content_digest(text, context):
    value = normalize_text(text) + "\x1f" + normalize_text(context or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TextWorkflow:
    def __init__(self, repository):
        self.repository = repository

    # ------------------------------------------------------------------ 导入
    def ingest(self, values):
        if not isinstance(values, list):
            raise TypeError("导入内容必须是记录列表")
        incoming = [TextRecord.from_dict(value).to_dict() for value in values]
        existing = self.repository.read()
        current = {record["record_id"]: record for record in existing}
        for record in incoming:
            previous = current.get(record["record_id"])
            if previous is None or record["revision"] >= previous["revision"]:
                record["text"] = normalize_text(record["text"])
                record["analysis"] = annotate(record)
                record["content_digest"] = content_digest(record["text"], record["context"])
                current[record["record_id"]] = record
        result = sorted(current.values(), key=lambda row: row["record_id"])
        # 复核事件只追加，不因导入被重建；无关批次不会清除教师确认的特殊语境。
        self.repository.replace(result)
        return {"received": len(incoming), "stored": len(result)}

    def list_records(self, source=None):
        records = self.repository.read()
        if source:
            records = [record for record in records if record["source"] == source]
        return records

    def overview(self, reviewer=None, source=None):
        """列表视图对非授权查看者隐藏原文，只保留可汇总的元数据。"""
        latest = self._latest_events()
        rows = []
        for record in self.list_records(source):
            reviewer_name = str(reviewer or "").strip()
            allowed = bool(reviewer_name) and (
                not record.get("authorized_reviewers") or reviewer_name in record["authorized_reviewers"]
            )
            evaluation = self._evaluate(record, latest.get(record["record_id"]))
            row = {
                "record_id": record["record_id"],
                "source": record["source"],
                "revision": record["revision"],
                "observed_at": record["observed_at"],
                "context": record["context"],
                "course_id": record.get("course_id", ""),
                "class_id": record.get("class_id", ""),
                "effective_signal": evaluation["effective_signal"],
                "needs_review": evaluation["needs_review"],
                "flags": record["analysis"]["flags"],
            }
            if allowed:
                row["text"] = record["text"]
            rows.append(row)
        return rows

    # ------------------------------------------------------------------ 复核
    def _latest_events(self):
        latest = {}
        for event in self.repository.read_review_events():
            latest[event["record_id"]] = event
        return latest

    def _record(self, record_id):
        for record in self.repository.read():
            if record["record_id"] == record_id:
                return record
        raise KeyError(f"未找到文本记录：{record_id}")

    @staticmethod
    def _check_authorization(record, reviewer, action):
        reviewer = str(reviewer or "").strip()
        if not reviewer:
            raise PermissionError(f"{action}需要复核人身份")
        allowed = record.get("authorized_reviewers") or []
        if allowed and reviewer not in allowed:
            raise PermissionError(f"复核人 {reviewer} 不在该记录的授权范围内")
        return reviewer

    def review(self, record_id, reviewer, decision, note=""):
        if decision not in DECISIONS:
            raise ValueError("复核决定必须是 confirm、dismiss 或 needs_context")
        record = self._record(record_id)
        reviewer = self._check_authorization(record, reviewer, "复核")
        note = str(note or "").strip()
        if decision in ("confirm", "dismiss") and not note:
            raise ValueError("确认或排除风险时必须记录复核依据")
        events = self.repository.read_review_events()
        event = {
            "event_id": f"review-{len(events) + 1:06d}",
            "record_id": record["record_id"],
            "revision": record["revision"],
            "content_digest": record["content_digest"],
            "decision": decision,
            "note": note,
            "reviewer": reviewer,
            "analysis_signal": record["analysis"]["signal"],
            "analysis_flags": list(record["analysis"]["flags"]),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        events.append(event)
        self.repository.replace(self.repository.read(), review_events=events)
        return self._evaluate(record, event)

    # ------------------------------------------------------------------ 判断
    def _evaluate(self, record, latest_event=None):
        analysis = record["analysis"]
        analysis_signal = analysis["signal"]
        review = None
        stale_review = False
        reasons = list(analysis["reasons"])

        if latest_event is not None:
            # 以内容摘要判断复核是否仍对应当前反馈；修订号单独变化但正文与
            # 上下文未变时，教师结论继续有效。
            if latest_event["content_digest"] == record.get("content_digest"):
                review = latest_event
            else:
                stale_review = True

        if review is not None:
            decision = review["decision"]
            if decision == "confirm":
                effective, source = "risk", "teacher"
            elif decision == "dismiss":
                effective, source = "not_risk", "teacher"
            else:
                effective, source = "undetermined", "teacher"
            reasons.append(f"教师复核：{DECISION_LABELS[decision]}")
            if review.get("note"):
                reasons.append(f"复核依据：{review['note']}")
            needs_review = decision == "needs_context"
        elif stale_review:
            # 文本或上下文已变更：旧复核结论作废，按新版本重新分析，
            # 但仍标记为需要教师再次确认。
            effective, source = analysis_signal, "stale_review"
            reasons.append("反馈内容或上下文已更新，原复核结论不再适用，已重新评估")
            if analysis_signal == "undetermined":
                reasons.append("系统无法确定情绪，需教师复核")
            needs_review = True
        else:
            effective = analysis_signal
            source = "auto"
            if analysis_signal == "undetermined":
                reasons.append("系统无法确定情绪，需教师复核")
            needs_review = analysis_signal == "undetermined" or not analysis["context_sufficient"]

        risk_level = analysis["risk_level"] if effective == "risk" else None
        if effective == "risk" and risk_level is None:
            risk_level = "confirmed"
        return {
            "record_id": record["record_id"],
            "course_id": record.get("course_id", ""),
            "class_id": record.get("class_id", ""),
            "revision": record["revision"],
            "analysis_signal": analysis_signal,
            "analysis_risk_level": analysis["risk_level"],
            "effective_signal": effective,
            "risk_level": risk_level,
            "source": source,
            "needs_review": needs_review,
            "stale_review": stale_review,
            "flags": analysis["flags"],
            "reasons": list(dict.fromkeys(reasons)),
            "review": review,
        }

    def evaluations(self):
        latest = self._latest_events()
        return [
            self._evaluate(record, latest.get(record["record_id"]))
            for record in self.list_records()
        ]

    # ------------------------------------------------------------ 待跟进名单
    def follow_ups(self):
        """有效判断为风险或待判断的记录；教师已确认非风险的记录不进入名单。"""
        rows = []
        for evaluation in self.evaluations():
            if evaluation["effective_signal"] not in ("risk", "undetermined"):
                continue
            rows.append({
                "record_id": evaluation["record_id"],
                "course_id": evaluation["course_id"],
                "class_id": evaluation["class_id"],
                "status": "risk" if evaluation["effective_signal"] == "risk" else "pending",
                "risk_level": evaluation["risk_level"],
                "needs_review": evaluation["needs_review"],
                "stale_review": evaluation["stale_review"],
                "reasons": evaluation["reasons"],
                "review_decision": evaluation["review"]["decision"] if evaluation["review"] else None,
            })
        return rows

    # -------------------------------------------------------------- 匿名汇总
    def summary(self, group_by="course_id", min_group_size=2):
        """不使用原文与记录身份的分组汇总；小样本组会被抑制，避免反推个人。"""
        if group_by not in ("course_id", "class_id", "source"):
            raise ValueError("汇总维度必须是 course_id、class_id 或 source")
        groups = {}
        for evaluation in self.evaluations():
            key = evaluation.get(group_by) or "未标注"
            group = groups.setdefault(key, {
                "group": key,
                "total": 0,
                "risk": 0,
                "not_risk": 0,
                "pending": 0,
                "teacher_reviewed": 0,
                "stale_review": 0,
            })
            group["total"] += 1
            signal = evaluation["effective_signal"]
            if signal == "risk":
                group["risk"] += 1
            elif signal == "not_risk":
                group["not_risk"] += 1
            else:
                group["pending"] += 1
            if evaluation["stale_review"]:
                group["stale_review"] += 1
            if evaluation["source"] == "teacher" and not evaluation["stale_review"]:
                group["teacher_reviewed"] += 1

        visible = []
        suppressed = 0
        for key in sorted(groups):
            group = groups[key]
            if group["total"] < min_group_size:
                suppressed += 1
                continue
            visible.append(group)
        return {
            "group_by": group_by,
            "min_group_size": min_group_size,
            "groups": visible,
            "suppressed_groups": suppressed,
            "note": "汇总只统计有效判断；待判断单独列出，不计入非风险。",
        }

    # ---------------------------------------------------------------- 个案依据
    def evidence(self, record_id, reviewer):
        """在授权范围内回看原文与线索；匿名汇总不经过此接口。"""
        record = self._record(record_id)
        reviewer = self._check_authorization(record, reviewer, "查看证据")
        evaluation = self._evaluate(record, self._latest_events().get(record_id))
        analysis = record["analysis"]
        return {
            "record_id": record["record_id"],
            "revision": record["revision"],
            "source": record["source"],
            "text": record["text"],
            "context": record["context"],
            "course_id": record.get("course_id", ""),
            "class_id": record.get("class_id", ""),
            "cues": analysis["cues"],
            "flags": analysis["flags"],
            "reasons": evaluation["reasons"],
            "effective_signal": evaluation["effective_signal"],
            "needs_review": evaluation["needs_review"],
            "stale_review": evaluation["stale_review"],
            "review": evaluation["review"],
            "review_history": [
                event for event in self.repository.read_review_events()
                if event["record_id"] == record_id
            ],
        }
