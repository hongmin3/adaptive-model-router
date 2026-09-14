## 실행 흐름
<!-- akela: id=execution-flow scope=all tier=must -->

User Prompt → 로컬 Complexity Score → Confidence 및 위험 floor → 현재 Provider의 로컬 모델 카탈로그 → 모델/Reasoning 추천 → 사용자 승인 → 실제 Codex 또는 Claude Code 실행 순서로 동작한다. 추천 단계에서는 모델이나 외부 AI API를 호출하지 않는다.

## 반복 작업 시 주의사항
<!-- akela: id=rerun-caveats scope=all tier=should -->

각 Prompt는 이전 요청의 난이도와 무관하게 새로 평가한다. CLI 모델 카탈로그는 실행 시마다 읽고, 확인되지 않은 한도나 reset time은 추측하지 않는다. Provider 실패 시 다른 Provider로 전환하지 않는다.

