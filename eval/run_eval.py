"""
결함 주입 eval harness 실행기.

cases.py의 각 케이스마다 redacted(요구사항 문장 뺀 버전)/compliant(포함 버전)
두 변형을 만들어 LangGraph 리뷰 파이프라인에 그대로 태운다. 핵심 지표는
"redacted 버전을 돌렸을 때, 우리가 뺀 요구사항에 해당하는 가이드라인 조항이
실제로 citations에 잡히는가" — recall이다.

flag(aligned/review_needed/conflict) 자체는 참고용으로만 같이 찍는다. 실제로
돌려본 결과 이 시스템은 사소한 이유로도 review_needed를 꽤 자주 준다는 걸
확인했기 때문에(Phase 4/5 노트), flag 하나만 보고 "적중/실패"를 가르면 노이즈가
크다. citations에 정확한 조항이 잡히는지가 더 깨끗한 신호다.
"""

import json
import sys
from pathlib import Path

import chromadb
from anthropic import Anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.graph import build_review_graph
from eval.cases import EVAL_CASES, build_variants
from observability.tracing import configure_tracing, flush_tracing

ROOT = Path(__file__).resolve().parent.parent
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"
OUTPUT_PATH = ROOT / "data" / "eval" / "eval_results.json"
REPORT_PATH = ROOT / "data" / "eval" / "eval_report.md"

TOP_K = 4


def _make_chunk(case: dict, variant: str, text: str) -> dict:
    return {
        "doc_id": "eval_synthetic",
        "section_number": case["id"],
        "section_title": case["topic"],
        "breadcrumb": f"[EVAL:{variant}] {case['topic']}",
        "page_start": 0,
        "page_end": 0,
        "char_count": len(text),
        "text": text,
    }


def _target_hit(case: dict, citations: list) -> bool:
    for c in citations:
        if c.doc_id != case["target_doc_id"]:
            continue
        haystack = f"{c.breadcrumb} {c.guideline_ref}"
        if any(kw in haystack for kw in case["target_section_keywords"]):
            return True
    return False


def run(use_hyde: bool = True):
    anthropic_client = Anthropic()
    chroma_client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    graph = build_review_graph(anthropic_client, chroma_client, use_hyde=use_hyde)
    tracer = configure_tracing()

    results = []
    for case in EVAL_CASES:
        redacted_text, compliant_text = build_variants(case)
        print(f"[{case['id']}] {case['topic']}", file=sys.stderr)

        case_result = {"id": case["id"], "topic": case["topic"], "variants": {}}

        for variant, text in [("redacted", redacted_text), ("compliant", compliant_text)]:
            chunk = _make_chunk(case, variant, text)
            # agent/run_protocol_review.py의 review_protocol_section과 같은 이유로
            # 감싼다 — 부모 span이 없으면 retrieve/compare/groundedness가 각자
            # 별개의 trace_id로 흩어져서 observability/analyze_traces.py가 하나의
            # 리뷰로 못 묶는다 (실제로 이 버그 때문에 eval 트레이스가 전부 조각나
            # 있었다).
            with tracer.start_as_current_span("eval_case") as case_span:
                case_span.set_attribute("eval.case_id", case["id"])
                case_span.set_attribute("eval.variant", variant)
                state = graph.invoke({"protocol_chunk": chunk, "top_k": TOP_K})
            citations = state["citations"]
            hit = _target_hit(case, citations)

            print(
                f"    {variant:10s} flag={state['llm_result']['flag']:14s} "
                f"conf={state['llm_result']['confidence']:.2f} target_hit={hit}",
                file=sys.stderr,
            )

            case_result["variants"][variant] = {
                "text": text,
                "flag": state["llm_result"]["flag"],
                "confidence": state["llm_result"]["confidence"],
                "target_hit": hit,
                "citations": [
                    {"doc_id": c.doc_id, "breadcrumb": c.breadcrumb, "grounded": c.grounded}
                    for c in citations
                ],
            }

        results.append(case_result)

    n = len(results)
    recall = sum(1 for r in results if r["variants"]["redacted"]["target_hit"]) / n
    compliant_hit_rate = sum(1 for r in results if r["variants"]["compliant"]["target_hit"]) / n

    summary = {
        "n_cases": n,
        "redacted_target_recall": recall,
        "compliant_target_hit_rate": compliant_hit_rate,
        "cases": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    n_flag_correct = sum(
        1
        for r in results
        if r["variants"]["redacted"]["flag"] != "aligned"
        and r["variants"]["compliant"]["flag"] == "aligned"
    )
    REPORT_PATH.write_text(
        _render_report(results, n, recall, compliant_hit_rate, n_flag_correct), encoding="utf-8"
    )

    print(
        f"\n완료: {n}건 중 redacted recall={recall:.0%}, "
        f"compliant hit rate={compliant_hit_rate:.0%} -> {OUTPUT_PATH}, {REPORT_PATH}",
        file=sys.stderr,
    )
    flush_tracing()


def _render_report(results: list, n: int, recall: float, compliant_hit_rate: float, n_flag_correct: int) -> str:
    lines = [
        "# Eval 리포트 — 결함 주입 테스트",
        "",
        "## 방법론",
        "",
        "실제 프로토콜 문장을 사람이 \"이건 위반이다\"라고 라벨링하는 대신, 가이드라인에",
        "실존하는 요구사항 8개(ICH E6(R2)/E8(R1) 6개 + FDA 1개 + MFDS 1개)를 골라 그",
        "요구사항을 충족하는 문단(compliant)과 핵심 문장만 뺀 문단(redacted)을 한 쌍씩",
        "만들었다. 정답은 우리가 직접 통제해서 만들었으므로 라벨 신뢰도는 100%다. 다만",
        "텍스트는 실제 프로토콜에서 발췌한 게 아니라 요구사항 하나만 깨끗하게 격리하려고",
        "새로 쓴 합성 문단이다 — 실제 프로토콜 문장은 여러 요구사항이 뒤섞여 있어 최소",
        "쌍을 만들기 어렵다. FDA/MFDS 케이스는 ICH 6개로 검증된 방식이 다른 문서",
        "스타일·언어(MFDS는 한글)에서도 통하는지 확인하려고 나중에 추가했다.",
        "",
        "핵심 지표는 두 가지다.",
        "- **target recall**: redacted 버전을 돌렸을 때, 뺀 요구사항에 해당하는 가이드라인",
        "  조항이 실제로 citations에 잡히는가.",
        "- **flag 정합성**: redacted는 aligned가 아닌 플래그를, compliant는 aligned를 받는가.",
        "",
        "## 결과 요약",
        "",
        f"- 케이스 수: {n}",
        f"- redacted target recall: **{recall:.0%}**",
        f"- compliant target hit rate: {compliant_hit_rate:.0%} (참고용 — 정상 텍스트에서도",
        "  같은 조항이 걸리는지)",
        f"- flag 정합성(redacted≠aligned & compliant=aligned): **{n_flag_correct}/{n}**",
        "",
        "## 개선 이력",
        "",
        "1차 실행에서는 target recall이 67%(4/6)였다. `data_audit_trail` 케이스를 까보니",
        "GLOSSARY 용어정의 청크(ICH E6R2 전체 청크의 41%, 65/159)가 실제 요구사항 조항과",
        "retrieval 순위를 놓고 경쟁해서 밀어내는 게 원인이었다 — 타겟 조항(5.5)이 top-30",
        "중 14위로 밀려나 있었다. `retrieval/build_vectorstore.py`에 `is_definition`",
        "메타데이터를 추가해 GLOSSARY 청크를 retrieval 후보에서 제외하도록 고치자, 같은",
        "쿼리에서 타겟 조항이 top_k=4 안 3위로 올라왔고, 재실행 결과 recall이 83%(5/6)로",
        "올랐다.",
        "",
        "## HyDE 실험 (기본값 off로 결론)",
        "",
        "남은 실패 케이스(`trial_injury_compensation`)를 고치려고 HyDE(Hypothetical",
        "Document Embeddings)를 시도했다 — 프로토콜 원문 대신 \"이 주제라면 가이드라인이",
        "이렇게 쓰여있을 것이다\"라는 가상 문장을 LLM으로 만들어 그걸로 retrieval.",
        "",
        "1. HyDE만 단독 적용: 특정 케이스는 고쳐졌지만 잘 되던 `data_audit_trail`이",
        "   깨졌다. 원인: HyDE로 만든 상세한 문장은 코퍼스 대다수 청크에 대해 절대",
        "   거리값 자체가 구조적으로 낮게 나오는 경향이 있어서(가이드라인 특유의",
        "   격식체 문장과 어휘가 겹치는 게 많아서), 원문 쿼리가 정확히 하나만 콕",
        "   집었어도 순위표가 흔들렸다.",
        "2. 원문+HyDE 쿼리를 합쳐서 검색(거리값 기준 병합)했더니 오히려 더 나빠졌다",
        "   (recall 50%). 서로 다른 쿼리의 절대 거리값은 스케일이 달라 직접 비교하면",
        "   안 된다는 걸 뒤늦게 확인 — HyDE 쪽이 절대 거리 경쟁에서 항상 유리해서",
        "   원문 쿼리의 결과를 통째로 밀어냈다.",
        "3. 병합 방식을 절대 거리 대신 순위 기반(Reciprocal Rank Fusion, RRF)으로",
        "   바꾸자 retrieval만 따로 테스트했을 때 recall이 92%(11/12)까지 올라갔다.",
        "4. 하지만 compare 단계까지 포함한 end-to-end eval에서는 오히려 67%(4/6)로,",
        "   HyDE를 안 쓴 baseline(83%)보다 낮았다. 3회 반복 모두 정확히 재현되는",
        "   안정적인 수치였다 (baseline도 3회 모두 83%로 안정적이었음 — 우연이",
        "   아니라는 뜻). retrieval 후보 집합이 매번 조금씩 달라지면서, compare",
        "   LLM이 최종적으로 인용하는 조항도 같이 흔들린 것으로 보인다.",
        "",
        "**결론**: retrieval만 떼어놓고 보면 HyDE+RRF가 분명히 더 낫다(67%→92%).",
        "하지만 실제로 배포되는 건 retrieval이 아니라 전체 파이프라인이고, 거기서는",
        "오히려 더 나쁜 결과가 3회 연속 재현됐다. 그래서 `agent/graph.py`의",
        "`use_hyde` 기본값을 `False`로 두고, 코드는 옵트인으로 남겨뒀다. 기법이",
        "이론적으로 맞다는 것과 이 파이프라인에서 실제로 이득이라는 건 다른",
        "질문이라는 걸 확인한 케이스.",
        "",
        "## Contextual embedding (채택)",
        "",
        "HyDE 이후 다른 방향으로 접근했다: 가이드라인 청크를 임베딩할 때 breadcrumb",
        "(섹션 계층 경로, 예: \"5 SPONSOR > 5.5 Trial Management, Data Handling\")를",
        "본문 앞에 붙여서 같이 임베딩했다. 원래는 본문 텍스트만 임베딩하고 있었는데,",
        "그러면 \"이 청크가 5.5 조항이다\"라는 제목 정보가 임베딩에 전혀 안 들어가서,",
        "서로 비슷한 SPONSOR 하위 조항들(5.0/5.1/5.15/5.18/5.5)이 본문 내용만으로",
        "구분돼야 했다.",
        "",
        "결과: HyDE 없이 순수 retrieval만 테스트하면 92%(11/12)로, 지금까지 시도한",
        "것 중 가장 높다 — 유일하게 남은 실패는 `trial_injury_compensation`의",
        "redacted 변형 하나뿐이었다(이건 텍스트 자체가 모호해서 못 찾는, HyDE 실험",
        "때부터 모든 설정에서 계속 실패하는 케이스). end-to-end recall은 83%로",
        "이전과 동일 — 이 케이스 하나가 여전히 compare 단계에서 인용되지 않아서다.",
        "",
        "HyDE와 다른 점: **end-to-end 수치를 깎아먹지 않으면서** retrieval",
        "품질을 올렸다. 추가 LLM 호출도 없고 코드도 몇 줄이라 유지비용도 낮다.",
        "그래서 이건 기본값으로 채택했다(`retrieval/build_vectorstore.py`).",
        "",
        "`trial_injury_compensation`의 redacted 케이스는 이번에도 못 잡았다 —",
        "네 가지 다른 설정(baseline/HyDE/HyDE+RRF/contextual embedding) 전부에서",
        "일관되게 실패하는 유일한 케이스로 확정됐다.",
        "",
        "## 짧은 청크(placeholder) 필터링",
        "",
        "이 케이스가 왜 안 잡히는지 원문 랭킹을 직접 까봤다. 타겟인 `5.8",
        "Compensation to Subjects and Investigators`(847자, 실제 보상/보험 요구사항",
        "서술) 대신, `6.14 Financing and Insurance`가 계속 더 가깝게 잡히고 있었다",
        "— 그런데 그 본문 전체가 \"Financing and insurance if not addressed in a",
        "separate agreement.\" 한 줄, 9단어짜리다. ICH E6(R2) 6장(CLINICAL TRIAL",
        "PROTOCOL AND PROTOCOL AMENDMENT(S))은 애초에 \"프로토콜에 이 항목들을",
        "넣어라\"는 체크리스트라서, 하위 조항 다수가 실제 요구사항이 아니라 이런",
        "한 줄짜리 프롬프트다 (평균 444자, 15개 중 4개가 100자 미만 — 실제 요구사항이",
        "담긴 5장 SPONSOR는 평균 845자, 41개 중 1개만 100자 미만).",
        "",
        "전체 가이드라인 코퍼스에서 100자 미만 청크를 다 훑어봤다(GLOSSARY, 문서",
        "서두 제외, 총 11개) — 참고문헌 목록 조각, 헤딩 줄바꿈 파편, 이런 체크리스트",
        "프롬프트뿐이었고 실질적 요구사항은 하나도 없었다. 그래서 `is_definition`과",
        "같은 방식으로 `is_low_content`(100자 미만) 메타데이터를 추가해 retrieval",
        "후보에서 제외했다.",
        "",
        "결과: `trial_injury_compensation`의 5.8 순위가 19위→16위로 거의 안",
        "움직였다 — `6.14`는 사라졌지만 그 자리를 다른 애매한 후보들이 채웠을",
        "뿐이었다. eval 전체로는 recall 83%로 동일(3회 반복 안정적), 유일한",
        "실패 케이스도 여전히 `trial_injury_compensation` 그대로다. 이 필터",
        "자체는 코퍼스 전체의 명백한 노이즈를 없애는 것이라 부작용 없이 채택했지만,",
        "**이 특정 케이스를 고치기엔 부족했다**는 게 솔직한 결론이다.",
        "",
        "**종합**: 다섯 가지 설정(baseline/HyDE/HyDE+RRF/contextual embedding/",
        "+low-content 필터) 전부에서 `trial_injury_compensation`만 일관되게",
        "실패한다. retrieval 쪽 개선으로는 한계에 도달한 것으로 보이고, 다음",
        "후보는 cross-encoder 재랭킹처럼 검색 후보 자체가 아니라 순위를 매기는",
        "방식을 바꾸는 접근, 또는 애초에 결측되기 쉬운 항목(보상·보험 등)을",
        "유형별로 미리 정의해두고 규칙 기반으로 보조 검색하는 방향이다.",
        "",
        "## FDA/MFDS 커버리지 확장",
        "",
        "ICH 6개 케이스로만 검증한 방식이 다른 문서에서도 통하는지 보려고 FDA,",
        "MFDS 요구사항을 하나씩 추가했다.",
        "",
        "- **FDA** (`pediatric_safety_study_size`, D. Safety Considerations —",
        "  소아 안전성 연구는 최소 100명·6개월 이상 노출): redacted/compliant",
        "  둘 다 정확히 잡힘.",
        "- **MFDS** (`mfds_development_plan_rationale`, 1 개발계획 — 이론적",
        "  근거·적응증·위험성 기술): compliant는 잡혔지만 redacted는 놓쳤다.",
        "  실제로 뭘 인용했는지 보니 타겟이 아니라 인접 섹션인 `5",
        "  임상시험계획서`(더 포괄적인 \"프로토콜에 뭘 담아야 하는지\" 섹션)를",
        "  인용했다 — ICH SPONSOR 이웃 조항 혼동, `trial_injury_compensation`의",
        "  모호한 텍스트 문제와 같은 계열의 실패 패턴이 한글 코퍼스에서도",
        "  똑같이 나타난다는 뜻이다. 언어를 바꿔도 근본 원인은 같다는",
        "  추가 증거로 남긴다.",
        "",
        "## 케이스별 상세 (아래는 기본 설정 — use_hyde=False, contextual embedding + is_low_content 필터 기준)",
        "",
    ]

    for r in results:
        red = r["variants"]["redacted"]
        comp = r["variants"]["compliant"]
        lines.append(f"### {r['id']} — {r['topic']}")
        lines.append("")
        lines.append(
            f"| variant | flag | confidence | target hit |\n|---|---|---|---|\n"
            f"| redacted | {red['flag']} | {red['confidence']:.2f} | {'✓' if red['target_hit'] else '✗'} |\n"
            f"| compliant | {comp['flag']} | {comp['confidence']:.2f} | {'✓' if comp['target_hit'] else '✗'} |"
        )
        lines.append("")
        if not red["target_hit"]:
            cited = ", ".join(f"{c['breadcrumb']}" for c in red["citations"]) or "(인용 없음)"
            lines.append(f"- redacted에서 타겟 조항을 못 찾음. 실제로 인용된 것: {cited}")
            lines.append("")

    lines.extend(
        [
            "## 남은 실패 케이스 원인 분석",
            "",
            "(이 섹션은 use_hyde=False 기본 설정 기준. HyDE로 이 케이스를 고쳐보려던",
            "시도와 그 결과는 위 'HyDE 실험' 섹션 참고.)",
            "",
            "**trial_injury_compensation (redacted만)** — 흥미로운 지점이다: redacted 텍스트",
            "(\"참가자에게 절차 비용을 청구하지 않는다\")가 그 자체로 너무 일반적이라 5.8",
            "Compensation 조항과 의미적으로 충분히 가깝지 않았다 (top_k=4 안에 안 들어옴).",
            "반면 compliant 버전은 상해 보상이라는 구체적 표현이 들어가면서 바로 잡혔다.",
            "즉 **결함이 있는 텍스트일수록 그 결함 때문에 오히려 관련 조항을 retrieval하기",
            "어려워지는 경향**이 있다는 뜻 — RAG 기반 검토 도구의 구조적 약점 중 하나로",
            "보인다. top_k를 늘리거나, 결측 항목을 추정하는 키워드 기반 보조 검색을",
            "곁들이는 방향으로 개선할 수 있다.",
            "",
            "**mfds_development_plan_rationale (redacted만)** — 같은 계열의 실패다.",
            "타겟(`1 개발계획`) 대신 인접 섹션(`5 임상시험계획서`)이 인용됐다 —",
            "위의 ICH SPONSOR 이웃 조항 혼동과 정확히 같은 패턴이 한글 코퍼스에서도",
            "재현된다는 뜻. 자세한 건 위 'FDA/MFDS 커버리지 확장' 섹션 참고.",
            "",
            "## 한계",
            "",
            "- N=8로 표본이 작다. 통계적으로 유의미한 수치라기보다 파이프라인의 약점을",
            "  찾아내는 진단 도구에 가깝다.",
            "- 합성 문단은 요구사항 하나만 깨끗하게 격리한 최소 쌍이라, 여러 이슈가 섞여",
            "  있는 실제 프로토콜 문장보다 판별이 쉬운 편이다. 그래서 flag 정합성이",
            "  실제 프로토콜 리뷰(Phase 4/5 기록상 confidence가 훨씬 들쭉날쭉했음)보다",
            "  깨끗하게 나왔을 가능성이 있다.",
        ]
    )

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-hyde", action="store_true", help="HyDE 쿼리 확장 없이 원문만으로 retrieval")
    args = parser.parse_args()
    run(use_hyde=not args.no_hyde)
