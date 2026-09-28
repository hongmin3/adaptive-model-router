# Adaptive Model Router 사양서

<!-- spec-template: v1 -->

| 항목 | 값 |
|---|---|
| Document Version | 0.1.0 |
| Last Updated | 2026-09-21 |
| Status | draft — 기존 계약 일부의 근거 기반 사양화 |

이 문서는 기존 README와 테스트가 명시한 계약을 보존한다. 코드에 맞추어 기존 약속을 바꾸지 않는다. 차이가 발견되면 SPEC / CODE MISMATCH로 기록한다. 제품 전체 요구사항을 빠짐없이 문서화했다고 주장하지 않는다.

## 1. 목적

사용자 프롬프트에 맞는 모델과 추론 강도를 로컬 규칙으로 추천한다.

## 2. 프로젝트 범위

이번 기준선에 포함: 선택기의 한도 대체·추론 지원·현재 모델 유지 계약.

이번 기준선의 확정 범위 밖: CLI 승인 UI와 실제 외부 CLI 실행, 전체 점수 규칙. 기존 기능을 제거하거나 변경한다는 뜻이 아니다.

## 5. 기능 요구사항

### REQ-CORE-001 한도 대체

현재 provider의 모델 목록과 사용량 상태를 입력받는다. FAST 후보가 WEEKLY_LIMIT_REACHED이고 같은 목록의 BALANCED 후보가 가용이면 BALANCED 후보를 선택하고 fallback_from 및 reset을 보존한다. 다른 provider로 전환하지 않는다.

관련 구현: `src/adaptive_model_router/selector.py`. 관련 테스트: `tests/test_selector.py`의 `test_limit_uses_same_provider_fallback`.

### REQ-CORE-002 지원 추론 강도

요청 추론 강도가 후보 모델의 지원 목록에 없으면 지원 가능한 가까운 강도로 조정한다. LOW/MEDIUM만 지원하는 fast 모델에 HIGH를 요청하면 MEDIUM을 반환한다.

관련 구현: `src/adaptive_model_router/selector.py`. 관련 테스트: `tests/test_selector.py`의 `test_unsupported_reasoning_uses_nearest_supported_level`.

### REQ-CORE-003 명시적 현재 모델

사용 가능한 현재 모델을 force_current로 지정하면 추천 프로필과 달라도 그 모델을 유지한다.

관련 구현: `src/adaptive_model_router/selector.py`. 관련 테스트: `tests/test_selector.py`의 `test_explicit_current_model_is_kept`.

## 9. 오류 처리 정책

가용 모델이 없으면 선택 결과를 UNAVAILABLE로 반환하며 다른 provider를 실행하지 않는다. CLI 실행 오류의 전체 정책은 이 문서의 확정 범위 밖이다.

## 11. 테스트 사양

격리된 개발 환경에서 프로젝트 의존성을 준비하고 저장소 루트에서 아래 명령을 실행한다. 현재 작업에서는 테스트 코드를 읽었으며 실제 실행 결과는 아직 확보하지 않았다. 테스트 파일의 설정이 운영 자원을 가리키지 않는지 먼저 확인한다.

```text
python run_tests.py -v
```

### TEST-CORE-001

REQ-CORE-001 검증: `tests/test_selector.py`의 `test_limit_uses_same_provider_fallback` fixture와 assertion을 실행한다. 기대 결과는 해당 Requirement의 입력별 결과이며 assertion 실패는 통과로 처리하지 않는다.

### TEST-CORE-002

REQ-CORE-002 검증: `tests/test_selector.py`의 `test_unsupported_reasoning_uses_nearest_supported_level` fixture와 assertion을 실행한다. 기대 결과는 해당 Requirement의 입력별 결과이며 assertion 실패는 통과로 처리하지 않는다.

### TEST-CORE-003

REQ-CORE-003 검증: `tests/test_selector.py`의 `test_explicit_current_model_is_kept` fixture와 assertion을 실행한다. 기대 결과는 해당 Requirement의 입력별 결과이며 assertion 실패는 통과로 처리하지 않는다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-CORE-001 | `src/adaptive_model_router/selector.py` | TEST-CORE-001: `tests/test_selector.py` | implemented |
| REQ-CORE-002 | `src/adaptive_model_router/selector.py` | TEST-CORE-002: `tests/test_selector.py` | implemented |
| REQ-CORE-003 | `src/adaptive_model_router/selector.py` | TEST-CORE-003: `tests/test_selector.py` | implemented |

implemented는 연결된 구현·테스트 소스가 존재한다는 뜻이며 실제 실행 통과를 뜻하지 않는다.

## 13. 미확정 사항

- 현재 문서는 선택기 핵심 계약의 첫 기준선이다. hook/native bridge/카탈로그/점수 규칙 전체의 요구사항 추적과 실제 두 CLI 검증은 확인 필요다.
- 이 기준선과 기존 전체 문서·기능의 누락 여부를 검토하기 전까지 프로젝트 전체 readiness 완료로 선언하지 않는다.
