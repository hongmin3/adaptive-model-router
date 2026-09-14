## 목적
<!-- akela: id=purpose scope=all tier=must -->

Adaptive Model Router는 별도 LLM 호출 없이 사용자 Prompt를 로컬 규칙으로 평가하고, 현재 CLI Provider 내부의 모델과 Reasoning/Effort를 추천한 뒤 승인 시에만 실제 CLI를 실행한다.

## 주요 구성
<!-- akela: id=components scope=all tier=should -->

- `router_config.json`: 점수, 문맥 조건, 임계값, 모델 역할 힌트의 단일 설정 원천.
- `src/adaptive_model_router/scorer.py`: Prompt를 로컬에서 점수화하며 외부 API나 AI CLI를 호출하지 않는다.
- `src/adaptive_model_router/catalog.py`: Codex/Claude Code가 로컬에서 공개하는 모델 정보와 현재 설정을 조회한다.
- `src/adaptive_model_router/selector.py`: Provider 내부 모델과 지원 Reasoning 단계를 선택한다.
- `src/adaptive_model_router/cli.py`: 추천 출력, 승인, 실제 CLI 실행을 담당한다.
- `tests/`: 문맥 경계, 위험 작업, 한도, Reasoning 지원 여부를 검증한다.
