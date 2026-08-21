"""
data/traces/spans.jsonl을 읽어서 비용/속도 리포트를 만든다.

OTel 계측(Phase 6)으로 span은 계속 쌓아왔지만, 실제로 "그래서 리뷰 한 건에
토큰이 얼마나 들고 시간이 얼마나 걸리는지" 분석해본 적은 없었다 — 이 스크립트가
그 격차를 메운다. 이미 있는 계측 데이터를 그대로 쓰는 거라 새 계측 코드는
필요 없다.

trace_id로 묶어서 "섹션 리뷰 1건" 단위를 복원한다. graph.invoke()를 호출할 때마다
(부모 span이 없으면) OTel이 새 trace_id를 자동으로 시작하므로, review_protocol_
section 래퍼가 없는 eval 실행도 trace_id 단위로는 똑같이 잡힌다 — 그래서
agent/run_protocol_review.py로 실행한 실제 프로토콜 리뷰와 eval/run_eval.py의
합성 케이스를 trace 안의 "protocol.doc_id" attribute 유무로 구분해서 따로 집계한다.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SPANS_PATH = ROOT / "data" / "traces" / "spans.jsonl"
REPORT_PATH = ROOT / "data" / "traces" / "cost_report.md"

# Claude Sonnet 5, 2026-08-31까지 적용되는 인트로 가격 (Anthropic 공식 요율).
INPUT_PRICE_PER_MTOK = 2.00
OUTPUT_PRICE_PER_MTOK = 10.00


def _parse_spans(path: Path) -> list[dict]:
    # ConsoleSpanExporter는 span마다 들여쓰기된 JSON을 이어붙여서 쓰기 때문에
    # 한 줄에 레코드 하나가 아니다 — JSONDecoder.raw_decode로 순서대로 읽는다.
    content = path.read_text(encoding="utf-8").strip()
    decoder = json.JSONDecoder()
    spans = []
    idx = 0
    while idx < len(content):
        rest = content[idx:].lstrip()
        if not rest:
            break
        idx = len(content) - len(rest)
        obj, end = decoder.raw_decode(content, idx)
        spans.append(obj)
        idx = end
    return spans


def _duration_ms(span: dict) -> float:
    start = datetime.fromisoformat(span["start_time"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(span["end_time"].replace("Z", "+00:00"))
    return (end - start).total_seconds() * 1000


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def analyze(spans: list[dict]) -> dict:
    by_trace = defaultdict(list)
    for s in spans:
        by_trace[s["context"]["trace_id"]].append(s)

    node_durations = defaultdict(list)
    reviews = []  # 완결된(=groundedness 노드가 있는) 리뷰 1건당 요약

    for trace_id, trace_spans in by_trace.items():
        for s in trace_spans:
            node_durations[s["name"]].append(_duration_ms(s))

        by_name = {s["name"]: s for s in trace_spans}
        if "groundedness" not in by_name or "compare" not in by_name:
            # BatchSpanProcessor는 프로세스가 다음 예약 flush 전에 끝나면 버퍼에
            # 남은 span을 잃어버린다 (flush_tracing() 도입 전 기록에 실제로 있었음,
            # 자세한 건 observability/tracing.py 참고). compare 없이 groundedness만
            # 살아남은 트레이스는 토큰/비용을 계산할 수 없는 반쪽짜리라 집계에서 뺀다.
            continue

        compare = by_name.get("compare")
        section = by_name.get("review_protocol_section")
        start = min(datetime.fromisoformat(s["start_time"].replace("Z", "+00:00")) for s in trace_spans)
        end = max(datetime.fromisoformat(s["end_time"].replace("Z", "+00:00")) for s in trace_spans)
        total_ms = (end - start).total_seconds() * 1000

        input_tok = compare["attributes"].get("llm.input_tokens", 0) if compare else 0
        output_tok = compare["attributes"].get("llm.output_tokens", 0) if compare else 0

        reviews.append(
            {
                "trace_id": trace_id,
                "is_real_protocol": section is not None,
                "doc_id": section["attributes"].get("protocol.doc_id") if section else None,
                "total_ms": total_ms,
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "status": by_name.get("escalate", by_name.get("auto_accept", {})).get(
                    "attributes", {}
                ).get("review.status"),
                "groundedness_score": by_name["groundedness"]["attributes"].get("groundedness.score"),
                "retrieval_hit": by_name["groundedness"]["attributes"].get("retrieval.hit"),
                "used_hyde": "hyde" in by_name,
            }
        )

    return {"node_durations": node_durations, "reviews": reviews}


def _cost(input_tok: int, output_tok: int) -> float:
    return input_tok / 1_000_000 * INPUT_PRICE_PER_MTOK + output_tok / 1_000_000 * OUTPUT_PRICE_PER_MTOK


def _render_report(data: dict) -> str:
    reviews = data["reviews"]
    node_durations = data["node_durations"]

    real = [r for r in reviews if r["is_real_protocol"]]
    synthetic = [r for r in reviews if not r["is_real_protocol"]]

    lines = [
        "# 비용/속도 리포트 (OTel 트레이스 기반)",
        "",
        f"`data/traces/spans.jsonl`에 쌓인 span {sum(len(v) for v in node_durations.values())}개,",
        f"완결된 리뷰 {len(reviews)}건(실제 프로토콜 {len(real)}건 + eval 합성 케이스",
        f"{len(synthetic)}건)을 집계했다. 가격은 Claude Sonnet 5 인트로 요율",
        f"(입력 ${INPUT_PRICE_PER_MTOK}/1M, 출력 ${OUTPUT_PRICE_PER_MTOK}/1M, 2026-08-31까지) 기준.",
        "",
        "## 노드별 소요시간 (전체 span 기준)",
        "",
        "| 노드 | 호출 수 | 평균(ms) | 총합(ms) |",
        "|---|---|---|---|",
    ]

    for name in sorted(node_durations, key=lambda n: -sum(node_durations[n])):
        vals = node_durations[name]
        lines.append(f"| {name} | {len(vals)} | {_mean(vals):.0f} | {sum(vals):.0f} |")

    for label, subset in [("실제 프로토콜 리뷰", real), ("eval 합성 케이스", synthetic)]:
        if not subset:
            continue
        total_tokens = sum(r["input_tokens"] + r["output_tokens"] for r in subset)
        total_cost = sum(_cost(r["input_tokens"], r["output_tokens"]) for r in subset)
        avg_ms = _mean([r["total_ms"] for r in subset])
        n_escalated = sum(1 for r in subset if r["status"] == "pending_human_review")
        avg_groundedness = _mean([r["groundedness_score"] for r in subset if r["groundedness_score"] is not None])
        n_hit = sum(1 for r in subset if r["retrieval_hit"])

        lines.extend(
            [
                "",
                f"## {label} ({len(subset)}건)",
                "",
                f"- 리뷰 1건당 평균 소요시간: {avg_ms:.0f}ms ({avg_ms/1000:.1f}초)",
                f"- 리뷰 1건당 평균 토큰: {total_tokens / len(subset):.0f} "
                f"(입력+출력 합계, hyde 노드 토큰은 미계측이라 별도)",
                f"- 리뷰 1건당 평균 비용: ${total_cost / len(subset):.4f}",
                f"- 총 비용: ${total_cost:.2f} ({len(subset)}건 합계)",
                f"- 에스컬레이션 비율: {n_escalated}/{len(subset)} ({n_escalated/len(subset):.0%})",
                f"- 평균 groundedness: {avg_groundedness:.2f}",
                f"- retrieval hit율: {n_hit}/{len(subset)} ({n_hit/len(subset):.0%})",
            ]
        )

    lines.extend(
        [
            "",
            "## 1,000건 프로토콜 섹션을 리뷰한다면",
            "",
        ]
    )
    if real:
        avg_cost_real = sum(_cost(r["input_tokens"], r["output_tokens"]) for r in real) / len(real)
        avg_ms_real = _mean([r["total_ms"] for r in real])
        lines.append(
            f"실제 프로토콜 리뷰 평균값(건당 ${avg_cost_real:.4f}, {avg_ms_real/1000:.1f}초) 기준으로 "
            f"외삽하면, 섹션 1,000건은 약 ${avg_cost_real*1000:.0f}, 순차 실행 시 "
            f"약 {avg_ms_real*1000/1000/60:.0f}분이 걸린다. (병렬화하지 않은 경우)"
        )

    lines.extend(
        [
            "",
            "## 알아둘 것",
            "",
            "- `hyde` 노드는 LLM 호출을 하지만 span에 토큰 사용량을 기록하지 않는다",
            "  (`build_hyde_query`가 usage를 반환하지 않아서) — hyde 사용 시 실제 비용은",
            "  여기 집계된 것보다 조금 더 높다. 계측 공백으로 남겨두고 여기 명시한다.",
            "- eval 합성 케이스는 프로토콜 원문보다 훨씬 짧은 텍스트라 토큰/시간이",
            "  실제 프로토콜 리뷰보다 낮게 나온다 — 두 그룹을 분리해서 집계한 이유다.",
            "- 이 분석을 만들다가 트레이스 계측 자체의 결함 두 개를 발견해서 고쳤다:",
            "  (1) `eval/run_eval.py`가 `graph.invoke()`를 부모 span 없이 호출해서",
            "  retrieve/compare/groundedness가 매번 서로 다른 trace_id로 흩어지고",
            "  있었다 — `eval_case`로 감싸서 고침. (2) `BatchSpanProcessor`는 프로세스가",
            "  다음 예약 flush 전에 끝나면 버퍼에 남은 span을 잃어버린다 — 짧게",
            "  끝나는 스크립트일수록 계측 데이터가 조용히 사라지고 있었다. 각 CLI",
            "  종료 직전에 `flush_tracing()`을 호출하도록 고침. 둘 다 고치기 전",
            "  기록은 이 리포트의 완결 집계에서 자동으로 빠진다(compare/groundedness",
            "  span이 둘 다 있는 trace만 집계하므로).",
        ]
    )

    return "\n".join(lines) + "\n"


def main():
    spans = _parse_spans(SPANS_PATH)
    data = analyze(spans)
    report = _render_report(data)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"완료: span {len(spans)}개 분석 -> {REPORT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
