"""
OpenTelemetry 계측 (메인) — trace/latency/token/retrieval hit·miss/groundedness.

CLAUDE.md §3.3: LangSmith만 쓰면 "LLM 앱 개발자 도구"에서 멈춘다. OTel은 벤더 중립
표준이라 trace/metric/log를 어디로든 보낼 수 있고, 이게 인프라 레벨 관측 이야기의
근거가 된다. 그래서 span 계측 자체는 OTel로 직접 하고, LangSmith는 개발 중 빠른
확인용으로 병행만 한다 (agent/graph.py의 LLM 호출에 @traceable을 붙여서 자동 연동).

1차 스코프: 핵심 span 몇 개(retrieve/compare/groundedness/route)에 latency,
retrieval hit·miss, groundedness score, token 사용량만 심는다. 대시보드는 후순위
— exporter를 ConsoleSpanExporter(로컬 JSONL 파일로 리다이렉트)로 뒀는데, OTLP
exporter로 바꾸기만 하면 Jaeger/Honeycomb/Grafana Tempo 등 어디로든 그대로
보낼 수 있다. "벤더 중립"이라는 설계 결정이 실제로 한 줄 교체로 증명되는 지점.
"""

from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

ROOT = Path(__file__).resolve().parent.parent
TRACE_LOG_PATH = ROOT / "data" / "traces" / "spans.jsonl"

_configured = False


def configure_tracing(service_name: str = "protocheck") -> trace.Tracer:
    global _configured
    if not _configured:
        TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = open(TRACE_LOG_PATH, "a", encoding="utf-8")

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter(out=out)))
        trace.set_tracer_provider(provider)
        _configured = True

    return trace.get_tracer(service_name)
