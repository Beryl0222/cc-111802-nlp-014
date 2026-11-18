import argparse
import json
from pathlib import Path

from .repository import RecordRepository
from .workflow import DECISIONS, TextWorkflow


def main():
    parser = argparse.ArgumentParser(description="本地教研文本处理工具")
    parser.add_argument("action", choices=("ingest", "list", "evaluate", "review", "follow-ups", "summary", "evidence"))
    parser.add_argument("path", nargs="?", default="fixtures/records.json")
    parser.add_argument("--state", default="var/records.json")
    parser.add_argument("--record", default=None)
    parser.add_argument("--reviewer", default=None)
    parser.add_argument("--decision", choices=DECISIONS, default=None)
    parser.add_argument("--note", default="")
    parser.add_argument("--group-by", default="course_id")
    parser.add_argument("--min-group-size", type=int, default=2)
    args = parser.parse_args()
    workflow = TextWorkflow(RecordRepository(args.state))
    try:
        if args.action == "ingest":
            result = workflow.ingest(json.loads(Path(args.path).read_text(encoding="utf-8")))
        elif args.action == "list":
            result = workflow.overview(args.reviewer)
        elif args.action == "evaluate":
            result = workflow.evaluations()
        elif args.action == "review":
            if not args.record or not args.reviewer or not args.decision:
                parser.error("review 需要 --record、--reviewer 和 --decision")
            result = workflow.review(args.record, args.reviewer, args.decision, args.note)
        elif args.action == "follow-ups":
            result = workflow.follow_ups()
        elif args.action == "summary":
            result = workflow.summary(args.group_by, args.min_group_size)
        else:
            if not args.record or not args.reviewer:
                parser.error("evidence 需要 --record 和 --reviewer")
            result = workflow.evidence(args.record, args.reviewer)
    except (KeyError, PermissionError, ValueError, TypeError) as error:
        parser.exit(1, f"错误：{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
