# Eval 리포트 — 결함 주입 테스트

## 방법론

실제 프로토콜 문장을 사람이 "이건 위반이다"라고 라벨링하는 대신, ICH E6(R2)/E8(R1)에
실존하는 요구사항 6개를 골라 그 요구사항을 충족하는 문단(compliant)과 핵심 문장만 뺀
문단(redacted)을 한 쌍씩 만들었다. 정답은 우리가 직접 통제해서 만들었으므로 라벨
신뢰도는 100%다. 다만 텍스트는 실제 프로토콜에서 발췌한 게 아니라 요구사항 하나만
깨끗하게 격리하려고 새로 쓴 합성 문단이다 — 실제 프로토콜 문장은 여러 요구사항이
뒤섞여 있어 최소 쌍을 만들기 어렵다.

핵심 지표는 두 가지다.
- **target recall**: redacted 버전을 돌렸을 때, 뺀 요구사항에 해당하는 가이드라인
  조항이 실제로 citations에 잡히는가.
- **flag 정합성**: redacted는 aligned가 아닌 플래그를, compliant는 aligned를 받는가.

## 결과 요약

- 케이스 수: 6
- redacted target recall: **83%**
- compliant target hit rate: 100% (참고용 — 정상 텍스트에서도
  같은 조항이 걸리는지)
- flag 정합성(redacted≠aligned & compliant=aligned): **6/6**

## 개선 이력

1차 실행에서는 target recall이 67%(4/6)였다. `data_audit_trail` 케이스를 까보니
GLOSSARY 용어정의 청크(ICH E6R2 전체 청크의 41%, 65/159)가 실제 요구사항 조항과
retrieval 순위를 놓고 경쟁해서 밀어내는 게 원인이었다 — 타겟 조항(5.5)이 top-30
중 14위로 밀려나 있었다. `retrieval/build_vectorstore.py`에 `is_definition`
메타데이터를 추가해 GLOSSARY 청크를 retrieval 후보에서 제외하도록 고치자, 같은
쿼리에서 타겟 조항이 top_k=4 안 3위로 올라왔고, 재실행 결과 recall이 83%(5/6)로
올랐다. 아래 케이스별 결과와 실패 분석은 이 수정을 반영한 최신 실행 기준이다.

## 케이스별 상세

### irb_approval_of_consent — 동의서는 사용 전 IRB/IEC 승인을 받아야 한다

| variant | flag | confidence | target hit |
|---|---|---|---|
| redacted | review_needed | 0.75 | ✓ |
| compliant | aligned | 0.85 | ✓ |

### sample_size_justification — 표본수 산출 근거(power analysis)를 명시해야 한다

| variant | flag | confidence | target hit |
|---|---|---|---|
| redacted | review_needed | 0.75 | ✓ |
| compliant | aligned | 0.85 | ✓ |

### withdrawal_criteria — 치료/시험절차 중단을 위한 명확한 기준이 있어야 한다

| variant | flag | confidence | target hit |
|---|---|---|---|
| redacted | review_needed | 0.75 | ✓ |
| compliant | aligned | 0.75 | ✓ |

### sae_reporting_timeline — 중대한 이상반응(SAE)은 즉시 스폰서에 보고해야 한다

| variant | flag | confidence | target hit |
|---|---|---|---|
| redacted | review_needed | 0.75 | ✓ |
| compliant | aligned | 0.85 | ✓ |

### data_audit_trail — 전자데이터 시스템은 밸리데이션·감사추적·접근통제가 있어야 한다

| variant | flag | confidence | target hit |
|---|---|---|---|
| redacted | review_needed | 0.75 | ✓ |
| compliant | aligned | 0.85 | ✓ |

### trial_injury_compensation — 시험 관련 상해 발생 시 치료비 보상 정책이 있어야 한다

| variant | flag | confidence | target hit |
|---|---|---|---|
| redacted | review_needed | 0.55 | ✗ |
| compliant | aligned | 0.85 | ✓ |

- redacted에서 타겟 조항을 못 찾음. 실제로 인용된 것: 6 CONDUCT, SAFETY MONITORING, AND REPORTING > 6.2 Participant Safety during Study Conduct > 6.2.2 Withdrawal Criteria

## 남은 실패 케이스 원인 분석

**trial_injury_compensation (redacted만)** — 흥미로운 지점이다: redacted 텍스트
("참가자에게 절차 비용을 청구하지 않는다")가 그 자체로 너무 일반적이라 5.8
Compensation 조항과 의미적으로 충분히 가깝지 않았다 (top_k=4 안에 안 들어옴).
반면 compliant 버전은 상해 보상이라는 구체적 표현이 들어가면서 바로 잡혔다.
즉 **결함이 있는 텍스트일수록 그 결함 때문에 오히려 관련 조항을 retrieval하기
어려워지는 경향**이 있다는 뜻 — RAG 기반 검토 도구의 구조적 약점 중 하나로
보인다. top_k를 늘리거나, 결측 항목을 추정하는 키워드 기반 보조 검색을
곁들이는 방향으로 개선할 수 있다.

## 한계

- N=6으로 표본이 작다. 통계적으로 유의미한 수치라기보다 파이프라인의 약점을
  찾아내는 진단 도구에 가깝다.
- 합성 문단은 요구사항 하나만 깨끗하게 격리한 최소 쌍이라, 여러 이슈가 섞여
  있는 실제 프로토콜 문장보다 판별이 쉬운 편이다. 그래서 flag 정합성이
  6/6으로 실제 프로토콜 리뷰(Phase 4/5 기록상 confidence가 훨씬 들쭉날쭉했음)
  보다 깨끗하게 나왔을 가능성이 있다.
