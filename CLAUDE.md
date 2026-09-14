# CLAUDE.md — adaptive-model-router

LLM 호출 없이 로컬 규칙으로 CLI 모델과 Reasoning을 추천하고 승인 후 실행하는 Hybrid Router

## Akela Context

Follow `akela/PROTOCOL.md` for every task.

## Project Root 탐색

이 프로젝트 하위 어디에서 작업하든(예: `src/`, `scripts/`, `tests/`) 먼저 현재 위치에서 상위로 `akela.json`을 탐색해 가장 가까운 Project Root를 식별하고, 그 Root의 `knowledge/`·`akela/PROTOCOL.md`를 사용한다. 하위 디렉터리에 별도 `akela.json`/`knowledge/`를 새로 만들지 않는다. 필요하면 `scripts/find-project-root.ps1`을 사용한다.

이 프로젝트를 Workspace 밖에서 단독으로 Clone해도 이 파일과 `akela.json`/`knowledge/`만으로 동일하게 동작해야 한다 (상위 Workspace 경로에 대한 의존성 없음).
