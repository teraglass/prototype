"""
검토 리포트 포맷팅 (Phase 7).

data/reviews/<doc_id>.json (Phase 4/5가 만든 raw 결과)를 사람이 읽는 Markdown
리포트로 바꾼다. CLAUDE.md §1 스코프 경계: 완벽한 대시보드가 목표가 아니므로
정적 Markdown 하나로 충분 — "판정"이 아니라 "검토 포인트 + 출처"를 사람이
훑어볼 수 있게 정리하는 것까지가 이 리포트의 역할이다.

가장 위에 에스컬레이션 큐(사람 검토가 아직 안 끝난 항목)를 심각도순으로 올려서,
바쁜 검토자가 위에서부터만 읽어도 우선순위가 맞게 만들었다.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from retrieval.section_review import SectionReview

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
REVIEWS_DIR = ROOT / "data" / "reviews"
REPORTS_DIR = ROOT / "data" / "reports"

FLAG_LABEL = {"conflict": "⛔ 충돌 가능", "review_needed": "⚠ 검토 필요", "aligned": "✅ 정합"}
FLAG_SEVERITY = {"conflict": 0, "review_needed": 1, "aligned": 2}


def load_reviews(doc_id: str) -> list[SectionReview]:
    path = REVIEWS_DIR / f"{doc_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [SectionReview.from_dict(r) for r in raw]


def _citation_lines(review: SectionReview) -> list[str]:
    lines = []
    for c in review.citations:
        mark = "✓" if c.grounded else "✗ 근거 미확인"
        lines.append(
            f"  - [{mark}] **{c.doc_id}** — {c.breadcrumb} (p{c.page_start}-{c.page_end})\n"
            f"    > {c.quoted_evidence}"
        )
    return lines


def _section_block(review: SectionReview, include_status: bool = True) -> str:
    lines = [
        f"### {review.protocol_breadcrumb}",
        f"{FLAG_LABEL.get(review.flag, review.flag)} · 확신도 {review.confidence:.2f} · "
        f"groundedness {review.groundedness_score:.2f}",
        "",
        review.rationale,
    ]
    citation_lines = _citation_lines(review)
    if citation_lines:
        lines.append("")
        lines.append("**근거 (가이드라인 원문 발췌):**")
        lines.extend(citation_lines)
    if include_status and review.human_decision:
        lines.append("")
        lines.append(f"**사람 검토 결과:** `{review.human_decision}`" + (f" — {review.human_note}" if review.human_note else ""))
    return "\n".join(lines)


def generate_report(doc_id: str) -> str:
    reviews = load_reviews(doc_id)

    n_total = len(reviews)
    n_by_flag = {f: sum(1 for r in reviews if r.flag == f) for f in FLAG_LABEL}
    pending = [r for r in reviews if r.needs_human_review and not r.human_decision]
    resolved = [r for r in reviews if r.human_decision]
    avg_groundedness = sum(r.groundedness_score for r in reviews) / n_total if n_total else 1.0

    pending_sorted = sorted(
        pending, key=lambda r: (FLAG_SEVERITY.get(r.flag, 9), r.confidence)
    )

    parts = [
        f"# 프로토콜 검토 리포트 — {doc_id}",
        "",
        "> 이 리포트는 규제 승인 가능 여부를 판정하지 않습니다. 프로토콜 섹션과 규제",
        "> 가이드라인을 대조해 검토가 필요해 보이는 지점과 그 출처를 정리한 것으로,",
        "> 최종 판단은 사람이 합니다.",
        "",
        "## 요약",
        "",
        f"- 검토 섹션: {n_total}건",
        f"- 정합/검토필요/충돌: {n_by_flag.get('aligned',0)} / {n_by_flag.get('review_needed',0)} / {n_by_flag.get('conflict',0)}",
        f"- 평균 groundedness: {avg_groundedness:.2f}",
        f"- 사람 검토 대기: {len(pending)}건 (처리 완료: {len(resolved)}건)",
        "",
    ]

    parts.append("## 🔺 사람 검토 대기 큐 (심각도순)")
    parts.append("")
    if not pending_sorted:
        parts.append("_대기 중인 항목 없음._")
    else:
        for r in pending_sorted:
            parts.append(_section_block(r))
            parts.append("")

    if resolved:
        parts.append("## 처리 완료된 항목")
        parts.append("")
        for r in resolved:
            parts.append(_section_block(r))
            parts.append("")

    parts.append("## 전체 섹션 (참고용)")
    parts.append("")
    for r in reviews:
        status = "🔺 대기" if (r.needs_human_review and not r.human_decision) else ("✔ 처리됨" if r.human_decision else "자동승인")
        parts.append(f"- [{FLAG_LABEL.get(r.flag, r.flag)}] {r.protocol_breadcrumb} — {status}")

    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description="검토 리포트를 Markdown으로 포맷팅")
    parser.add_argument("--doc-id", required=True)
    args = parser.parse_args()

    report = generate_report(args.doc_id)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"{args.doc_id}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"리포트 생성: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
