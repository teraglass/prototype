# protocheck

임상시험 프로토콜을 FDA/ICH/MFDS 규제 가이드라인과 대조해서, 검토 포인트와 근거를
사람에게 제시하는 에이전트 파이프라인이다. 규제 판정 도구는 아니다. "이 항목이
어떤 규정과 관련되며 검토가 필요하다"까지만 말하고, 최종 판단은 사람이 한다.

설계 의도와 각 결정의 이유는 [CLAUDE.md](CLAUDE.md)에 더 자세히 적어뒀다. 여기는
켜고 돌리는 법 위주로 정리한다.

## 신약 개발 프로세스에서 이 프로젝트의 위치

임상시험은 대략 이런 순서로 진행된다.

1. 비임상시험(동물실험 등)으로 독성·약리 데이터 확보
2. 임상개발팀·생물통계팀·의학저술가가 그 데이터를 근거로 프로토콜(임상시험계획서) 작성
3. **Regulatory Affairs(RA)팀이 프로토콜을 ICH GCP·FDA·MFDS 요구사항과 조항별로 대조·검토** ← 이 프로젝트가 보조하는 지점
4. 시험기관 IRB의 윤리적 승인
5. 규제기관에 IND 등으로 제출 → 승인 또는 보완요청
6. 실제 임상시험 수행, QA팀이 GCP 준수 여부를 감사(audit)
7. 데이터 분석 및 Clinical Study Report 작성
8. 여러 상의 데이터를 모아 시판허가(NDA/BLA) 신청 — 여기서도 규제 대조가 다시 필요

이 프로젝트는 3번, 즉 RA팀이 프로토콜 초안을 규제 요구사항과 손으로 대조하던 작업을
1차로 걸러주는 보조 도구다. QA팀의 "시험이 실제로 프로토콜대로 수행되는지" 감사나,
법무팀의 특허 출원(별개의 지식재산권 보호 절차로, 보통 후보물질 발견 초기에 진행됨)과는
다른 트랙이다.

## 아키텍처

```
[수집]  ClinicalTrials.gov API v2 ──▶ 프로토콜 PDF
        FDA / ICH / MFDS 가이드라인 ─▶ 규제 코퍼스
          │
[청킹]  섹션 인식 청킹 (폰트 스타일 기반 헤딩 판별, 언어 무관)
          │
[임베딩] multilingual-e5-large → Chroma (guideline / protocol 컬렉션 분리)
          │
[에이전트] LangGraph: retrieve → compare → groundedness →(조건 분기)→ escalate | auto_accept
          │
[출력]  Markdown 리포트 (에스컬레이션 큐 + 근거 출처) + human-in-the-loop 처리 CLI
          │
[관측]  OpenTelemetry (메인, 로컬 JSONL) + LangSmith (보조)
```

프로토콜과 가이드라인 컬렉션을 굳이 나눈 이유는, 둘의 역할이 다르기 때문이다.
프로토콜은 검토 대상이고 가이드라인은 대조 기준이라 retrieval도 항상 프로토콜에서
가이드라인 방향으로만 쓰인다.

## 설치

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env
```

`.env`에는 두 가지를 채운다.

- `ANTHROPIC_API_KEY` — 필수. 섹션 대조 LLM 호출에 쓴다.
- `LANGCHAIN_API_KEY` — 선택. LangSmith 보조 트레이싱용이고, 없어도 OTel 계측은 그대로 동작한다.

임베딩(`intfloat/multilingual-e5-large`)은 API 키 없이 로컬에서 돈다. 다만 첫 실행
때 모델을 자동으로 내려받아서(~2GB) 시간이 좀 걸린다.

## 사용법

### 1. 데이터 수집

```bash
python ingestion/fetch_clinicaltrials.py --condition epilepsy --count 10 --output data/epilepsy_trials_metadata.json
python ingestion/fetch_guidelines.py
```

`data/epilepsy_trials_metadata.json`의 `downloadUrl`로 실제 프로토콜 PDF를 받아서
`data/protocols/`에 넣는다. 위 스크립트는 메타데이터와 URL까지만 만들고, 다운로드는
별도 단계다.

### 2. 청킹

```bash
python chunking/run_chunking.py
```

`data/guidelines/`와 `data/protocols/`의 모든 PDF를 섹션 단위로 잘라서 `data/chunks/`에 저장한다.

### 3. 벡터스토어 구축

```bash
python retrieval/build_vectorstore.py
python retrieval/query_vectorstore.py --collection guideline --query "informed consent process" --top-k 5
```

### 4. 프로토콜 검토 (LangGraph)

```bash
python agent/run_protocol_review.py --doc-id NCT03958331_Prot_000
```

`data/reviews/<doc_id>.json`에 섹션별로 flag, 근거, groundedness, 에스컬레이션 여부가 저장된다.

### 5. 리포트와 human-in-the-loop

```bash
python agent/generate_report.py --doc-id NCT03958331_Prot_000
python agent/resolve_escalation.py --doc-id NCT03958331_Prot_000 --list
python agent/resolve_escalation.py --doc-id NCT03958331_Prot_000 --index 3 --decision confirmed --note "..."
```

`data/reports/<doc_id>.md`가 사람이 읽는 최종 산출물이다.

### 관측

OpenTelemetry span은 `data/traces/spans.jsonl`에 쌓인다. exporter만 바꾸면
Jaeger나 Honeycomb 같은 다른 백엔드로도 그대로 보낼 수 있다. LangSmith는
`LANGCHAIN_API_KEY`가 설정돼 있으면 `protocheck` 프로젝트로 자동 트레이싱된다.

## 프로젝트 구조

```
ingestion/      ClinicalTrials.gov / FDA·ICH·MFDS 수집 스크립트
chunking/       섹션 인식 청킹 (도메인 로직, 직접 구현)
retrieval/      임베딩, 벡터스토어, retrieval+대조 순수 함수
agent/          LangGraph 배선, 리포트 생성, human-in-the-loop CLI
observability/  OpenTelemetry 계측 설정
data/           수집물 · 청크 · 벡터스토어 · 리뷰 · 리포트 · 트레이스
```

## 상태

CLAUDE.md의 Phase 0~7을 전부 구현했다. 표본 검증은 뇌전증(epilepsy) 도메인
프로토콜 10건과 FDA/ICH/MFDS 가이드라인 4건으로 진행했다. 코퍼스를 늘리려면
`ingestion/` 스크립트의 조건이나 문서 목록만 확장하면 된다.
