"""
사람 검토 대기 큐를 실제로 처리하는 CLI (Phase 7의 human-in-the-loop 경로).

에스컬레이션이 "리포트에 표시만 되고 끝"이면 §3.5에서 말한 human-in-the-loop이
아니라 그냥 경고 배지다. 이 스크립트는 사람이 실제로 판단(decision)을 내려서
그 결과가 리포트/원본 데이터에 반영되게 하는, 최소한이지만 진짜 동작하는 경로다.

--list로 대기 큐를 보고, --index로 항목을 골라 --decision(confirmed/false_positive/
approved_as_is)을 기록한다. 기록 후에는 리포트를 바로 재생성해서 처리 완료 섹션으로
옮겨준다.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.generate_report import generate_report
from retrieval.section_review import SectionReview

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
REVIEWS_DIR = ROOT / "data" / "reviews"
REPORTS_DIR = ROOT / "data" / "reports"

VALID_DECISIONS = ["confirmed", "false_positive", "approved_as_is"]


def load(doc_id: str) -> list[SectionReview]:
    path = REVIEWS_DIR / f"{doc_id}.json"
    return [SectionReview.from_dict(r) for r in json.loads(path.read_text(encoding="utf-8"))]


def save(doc_id: str, reviews: list[SectionReview]) -> None:
    path = REVIEWS_DIR / f"{doc_id}.json"
    path.write_text(
        json.dumps([r.to_dict() for r in reviews], indent=2, ensure_ascii=False), encoding="utf-8"
    )


def list_pending(reviews: list[SectionReview]) -> None:
    pending = [(i, r) for i, r in enumerate(reviews) if r.needs_human_review and not r.human_decision]
    if not pending:
        print("대기 중인 항목 없음.", file=sys.stderr)
        return
    for i, r in pending:
        print(
            f"[{i}] {r.protocol_breadcrumb} — flag={r.flag} conf={r.confidence:.2f} "
            f"groundedness={r.groundedness_score:.2f}",
            file=sys.stderr,
        )
        print(f"     {r.rationale[:120]}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="에스컬레이션 큐 처리")
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--list", action="store_true", help="대기 중인 항목 목록만 보기")
    parser.add_argument("--index", type=int, help="처리할 항목의 인덱스 (--list로 확인)")
    parser.add_argument("--decision", choices=VALID_DECISIONS)
    parser.add_argument("--note", default=None)
    args = parser.parse_args()

    reviews = load(args.doc_id)

    if args.list or args.index is None:
        list_pending(reviews)
        return

    if not (0 <= args.index < len(reviews)):
        print(f"인덱스 범위 오류: {args.index} (0~{len(reviews)-1})", file=sys.stderr)
        sys.exit(1)

    target = reviews[args.index]
    if not target.needs_human_review:
        print(f"경고: [{args.index}] {target.protocol_breadcrumb}은 에스컬레이션 대상이 아니었음", file=sys.stderr)

    if args.decision is None:
        print("--decision 이 필요함 (confirmed/false_positive/approved_as_is)", file=sys.stderr)
        sys.exit(1)

    target.human_decision = args.decision
    target.human_note = args.note

    save(args.doc_id, reviews)
    print(f"[{args.index}] {target.protocol_breadcrumb} -> {args.decision} 기록됨", file=sys.stderr)

    report = generate_report(args.doc_id)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{args.doc_id}.md").write_text(report, encoding="utf-8")
    print("리포트 갱신됨", file=sys.stderr)


if __name__ == "__main__":
    main()
