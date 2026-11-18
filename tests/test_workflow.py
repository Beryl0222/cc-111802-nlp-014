import json
import tempfile
import unittest
from pathlib import Path

from nlp_student_feedback_sentiment.repository import RecordRepository
from nlp_student_feedback_sentiment.text import annotate, normalize_text, tokenize
from nlp_student_feedback_sentiment.workflow import TextWorkflow


def record(record_id="lesson-1", text="原稿 甲乙", context="课堂", revision=1, **extra):
    value = {
        "record_id": record_id,
        "source": "school-synthetic",
        "revision": revision,
        "observed_at": "2026-11-18T09:00:00Z",
        "text": text,
        "context": context,
        "course_id": "course-a",
        "class_id": "class-1",
        "authorized_reviewers": ["teacher-a", "teacher-b"],
    }
    value.update(extra)
    return value


class TextWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = RecordRepository(Path(self.directory.name) / "records.json")
        self.workflow = TextWorkflow(self.repository)

    def test_normalization_and_tokenization(self):
        self.assertEqual(normalize_text("  Ａ  B\nC  "), "A B C")
        self.assertEqual(tokenize("语文课 A2。"), ["语", "文", "课", "A", "2", "。"])

    def test_ingest_persists_analysis_and_uses_newer_revision(self):
        first = record()
        second = record(revision=2, text="修订稿 乙丙")
        self.assertEqual(self.workflow.ingest([first]), {"received": 1, "stored": 1})
        self.workflow.ingest([second, first])
        result = self.workflow.list_records()[0]
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["text"], "修订稿 乙丙")
        self.assertEqual(result["analysis"]["token_count"], 5)

    def test_invalid_records_are_rejected_without_partial_write(self):
        valid = record(record_id="one", text="有效文本")
        self.workflow.ingest([valid])
        invalid = {**valid, "record_id": "two", "observed_at": "bad-time"}
        with self.assertRaises(ValueError):
            self.workflow.ingest([valid, invalid])
        self.assertEqual([row["record_id"] for row in self.repository.read()], ["one"])

    def test_source_filter_and_fixture_are_local(self):
        fixture = json.loads(Path("fixtures/records.json").read_text(encoding="utf-8"))
        self.workflow.ingest(fixture)
        self.assertEqual(len(self.workflow.list_records("school-synthetic")), 2)
        self.assertEqual(self.workflow.list_records("missing"), [])


class EmotionAnalysisTests(unittest.TestCase):
    def analysis(self, text, context="数学课 第三章"):
        return annotate({"text": text, "context": context})

    def test_direct_cues_are_detected_with_context(self):
        result = self.analysis("这题我不会，谁来教教我。")
        self.assertEqual(result["signal"], "risk")
        self.assertEqual({cue["category"] for cue in result["cues"]}, {"求助"})

    def test_missing_context_is_pending_not_zero_risk(self):
        result = annotate({"text": "这次没考好，难受。", "context": ""})
        self.assertEqual(result["signal"], "risk")
        self.assertIn("缺少上下文", result["flags"])

        unclear = annotate({"text": "还行吧。", "context": ""})
        self.assertEqual(unclear["signal"], "undetermined")
        self.assertIn("缺少上下文", unclear["flags"])

        workflow = TextWorkflow(RecordRepository(Path(tempfile.mkdtemp()) / "r.json"))
        workflow.ingest([record(text="还行吧。", context="")])
        evaluation = workflow.evaluations()[0]
        self.assertEqual(evaluation["effective_signal"], "undetermined")
        self.assertTrue(evaluation["needs_review"])
        self.assertNotIn(evaluation["effective_signal"], ("not_risk",))

    def test_negation_and_terms_and_jokes_are_ambiguous(self):
        self.assertEqual(self.analysis("没有压力，我很喜欢这门课。")["signal"], "not_risk")
        self.assertEqual(self.analysis("我非常喜欢这门课。")["signal"], "not_risk")
        self.assertEqual(self.analysis("我不太喜欢这门课。")["signal"], "risk")

        term = self.analysis("这题里的“崩溃”是术语，老师让我们分析作者情绪。", "语文课")
        self.assertEqual(term["signal"], "undetermined")
        self.assertIn("引用或术语语境", term["flags"])

        joke = self.analysis("太烦了，明天还要考这个梗。", "同伴玩笑")
        self.assertEqual(joke["signal"], "undetermined")
        self.assertIn("同伴玩笑语境", joke["flags"])

        harmless_joke = self.analysis("太绝了，明天还要考这个梗。", "同伴玩笑")
        self.assertEqual(harmless_joke["signal"], "not_risk")
        self.assertIn("同伴玩笑语境", harmless_joke["flags"])

        no_cues = self.analysis("今天讲了第三章。")
        self.assertEqual(no_cues["signal"], "not_risk")

        # 上下文变化后不应再被旧结论掩盖。
        term_with_change = self.analysis("这题里的“崩溃”是术语，可我真的崩溃了。", "语文课 课后")
        self.assertEqual(term_with_change["signal"], "risk")

    def test_sarcasm_marker_blocks_negative_cue(self):
        result = self.analysis("老师讲得太好了，我完全不会。")
        self.assertEqual(result["signal"], "risk")

        result = self.analysis("我真棒，又听不懂了，呵呵。")
        self.assertEqual(result["signal"], "undetermined")
        self.assertIn("疑似反讽", result["flags"])


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = RecordRepository(Path(self.directory.name) / "records.json")
        self.workflow = TextWorkflow(self.repository)

    def test_teacher_decision_drives_summary_and_follow_ups(self):
        self.workflow.ingest([
            record("r1", text="老师讲得太好了，我完全不会。", course_id="c1"),
            record("r2", text="好烦，完全不想学。", course_id="c1"),
            record("r3", text="我喜欢这门课。", course_id="c1"),
        ])
        self.workflow.review("r1", "teacher-a", "dismiss", "同伴间反话，已当面确认")
        self.workflow.review("r2", "teacher-a", "confirm", "连续两周缺交作业")

        follow_ups = {row["record_id"]: row for row in self.workflow.follow_ups()}
        self.assertNotIn("r1", follow_ups)
        self.assertEqual(follow_ups["r2"]["status"], "risk")
        self.assertEqual(follow_ups["r2"]["review_decision"], "confirm")

        summary = self.workflow.summary("course_id")
        group = summary["groups"][0]
        self.assertEqual(group["risk"], 1)
        self.assertEqual(group["not_risk"], 2)
        self.assertEqual(group["pending"], 0)

        # 教师作出的确认也同步到汇总，不再沿用自动判断。
        self.assertEqual(
            {row["effective_signal"] for row in self.workflow.evaluations()},
            {"risk", "not_risk"},
        )

    def test_summary_counts_pending_separately(self):
        self.workflow.ingest([
            record("r1", text="这题我不会。", course_id="c1"),
            record("r2", text="这题里的“崩溃”是术语。", context="语文课", course_id="c1"),
        ])
        group = self.workflow.summary("course_id")["groups"][0]
        self.assertEqual((group["risk"], group["not_risk"], group["pending"]), (1, 0, 1))

    def test_editing_text_or_context_reopens_decision(self):
        self.workflow.ingest([record("r1", text="这题里的“崩溃”是术语。", context="语文课")])
        self.workflow.review("r1", "teacher-a", "dismiss", "术语引用，非情绪表达")
        self.assertEqual(self.workflow.evaluations()[0]["effective_signal"], "not_risk")

        # 补充上下文：之前被当成术语，现在语境变化，需要重新判断。
        self.workflow.ingest([record("r1", text="这题里的“崩溃”是术语，但我真的崩溃了。",
                                     context="语文课 课后", revision=2)])
        evaluation = self.workflow.evaluations()[0]
        self.assertEqual(evaluation["effective_signal"], "risk")
        self.assertTrue(evaluation["stale_review"])
        self.assertIsNone(evaluation["review"])
        pending = {row["record_id"]: row for row in self.workflow.follow_ups()}
        self.assertEqual(pending["r1"]["status"], "risk")
        summary = self.workflow.summary("course_id", min_group_size=1)["groups"][0]
        self.assertEqual((summary["risk"], summary["not_risk"], summary["pending"]), (1, 0, 0))

    def test_unrelated_batches_preserve_teacher_context(self):
        self.workflow.ingest([record("r1", text="老师讲得太好了，我完全不会。")])
        self.workflow.review("r1", "teacher-a", "dismiss", "课堂互怼玩笑")
        before = self.workflow.evidence("r1", "teacher-a")

        self.workflow.ingest([record("other", text="我喜欢这门课。")])
        after = self.workflow.evidence("r1", "teacher-a")
        self.assertEqual(after["effective_signal"], "not_risk")
        self.assertEqual(after["review"], before["review"])
        self.assertEqual(len(after["review_history"]), 1)

        # 同一版本重放不会清空复核历史。
        self.workflow.ingest([record("r1", text="老师讲得太好了，我完全不会。")])
        self.assertEqual(len(self.workflow.evidence("r1", "teacher-a")["review_history"]), 1)

    def test_evidence_requires_authorization_and_summary_is_anonymous(self):
        self.workflow.ingest([
            record("r1", text="这题我不会。", authorized_reviewers=["teacher-a"]),
            record("r2", text="我喜欢这门课。", authorized_reviewers=["teacher-a"]),
        ])
        with self.assertRaises(PermissionError):
            self.workflow.evidence("r1", "outsider")
        with self.assertRaises(PermissionError):
            self.workflow.review("r1", "outsider", "dismiss", "x")
        evidence = self.workflow.evidence("r1", "teacher-a")
        self.assertIn("这题我不会", evidence["text"])

        summary = self.workflow.summary("class_id")
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("r1", serialized)
        self.assertNotIn("这题我不会", serialized)
        self.assertNotIn("喜欢这门课", serialized)

    def test_small_group_is_suppressed_and_review_needs_reason(self):
        self.workflow.ingest([record("r1", text="这题我不会。")])
        summary = self.workflow.summary("course_id", min_group_size=2)
        self.assertEqual(summary["groups"], [])
        self.assertEqual(summary["suppressed_groups"], 1)
        with self.assertRaises(ValueError):
            self.workflow.review("r1", "teacher-a", "confirm")

    def test_needs_context_stays_pending_until_teacher_reconsiders(self):
        self.workflow.ingest([record("r1", text="这题里的“崩溃”是术语。", context="语文课")])
        self.workflow.review("r1", "teacher-a", "needs_context", "需要知道这是哪篇课文")
        evaluation = self.workflow.evaluations()[0]
        self.assertEqual(evaluation["effective_signal"], "undetermined")
        self.assertEqual(evaluation["review"]["decision"], "needs_context")
        follow_up = self.workflow.follow_ups()[0]
        self.assertEqual(follow_up["status"], "pending")

        group = self.workflow.summary("course_id", min_group_size=1)["groups"][0]
        self.assertEqual((group["risk"], group["not_risk"], group["pending"]), (0, 0, 1))

    def test_latest_teacher_decision_wins_for_same_version(self):
        self.workflow.ingest([record("r1", text="这题里的“崩溃”是术语。", context="语文课")])
        self.workflow.review("r1", "teacher-a", "needs_context", "先补充课文信息")
        self.workflow.review("r1", "teacher-b", "dismiss", "已核对课文，属术语引用")
        evaluation = self.workflow.evaluations()[0]
        self.assertEqual(evaluation["effective_signal"], "not_risk")
        self.assertEqual(evaluation["review"]["reviewer"], "teacher-b")
        self.assertEqual(len(self.workflow.evidence("r1", "teacher-a")["review_history"]), 2)

    def test_overview_hides_text_unless_reviewer_is_authorized(self):
        self.workflow.ingest([record("r1", text="这题我不会。", authorized_reviewers=["teacher-a"])])
        anonymous = self.workflow.overview()[0]
        self.assertNotIn("text", anonymous)
        self.assertNotIn("这题我不会", json.dumps(anonymous, ensure_ascii=False))
        authorized = self.workflow.overview("teacher-a")[0]
        self.assertEqual(authorized["text"], "这题我不会。")
        outsider = self.workflow.overview("outsider")[0]
        self.assertNotIn("text", outsider)

    def test_revision_bump_without_content_change_keeps_decision(self):
        self.workflow.ingest([record("r1", text="老师讲得太好了，我完全不会。")])
        self.workflow.review("r1", "teacher-a", "dismiss", "已当面确认是玩笑")
        self.workflow.ingest([record("r1", text="老师讲得太好了，我完全不会。", revision=2)])
        evaluation = self.workflow.evaluations()[0]
        self.assertEqual(evaluation["effective_signal"], "not_risk")
        self.assertFalse(evaluation["stale_review"])

    def test_context_change_invalidates_decision(self):
        self.workflow.ingest([record("r1", text="老师讲得太好了，我完全不会。", context="课堂")])
        self.workflow.review("r1", "teacher-a", "dismiss", "已当面确认是玩笑")
        self.workflow.ingest([record("r1", text="老师讲得太好了，我完全不会。",
                                     context="课堂 随堂测验后", revision=2)])
        evaluation = self.workflow.evaluations()[0]
        self.assertEqual(evaluation["effective_signal"], "risk")
        self.assertTrue(evaluation["stale_review"])
        self.assertTrue(evaluation["needs_review"])


if __name__ == "__main__":
    unittest.main()
