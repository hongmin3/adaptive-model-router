# Adaptive Model Router

> **모델 사용량을 아끼기 위해 만든 Router가 모델을 하나 더 호출한다면, 정말 최적화일까?**

Adaptive Model Router는 이 질문에서 시작한 개인 AI 자동화 프로젝트입니다. 사용자의 작업 요청을 **로컬 규칙으로만 분석**하고, 적절한 모델 등급과 Reasoning/Effort를 추천한 다음, 사용자가 승인했을 때 비로소 Codex 또는 Claude Code를 실행합니다.

Router 판단 단계의 LLM/API 호출은 기본적으로 **0회**, 토큰 사용량도 **0에 가깝습니다**.

## 한눈에 보기

| 항목 | 구현 |
|---|---|
| 문제 | 모델 선택을 위한 별도 LLM 호출이 사용량과 지연을 증가시킴 |
| 접근 | 설정 가능한 Rule-based Complexity Score |
| 불확실한 요청 | 최고 모델 대신 `BALANCED + MEDIUM` 적용 |
| 모델 목록 | 실행 시 각 CLI가 공개하는 로컬 카탈로그/도움말에서 조회 |
| Provider 격리 | Codex는 OpenAI/Codex 안에서, Claude Code는 Claude 안에서만 선택 |
| 실행 시점 | 추천 확인 후 사용자가 승인한 작업에만 모델 호출 |
| 검증 | 문맥·위험도·길이·한도·Reasoning 지원 여부를 포함한 자동 테스트 |

## 제가 해결하고 싶었던 문제

AI CLI를 사용하다 보면 단순 README 수정에도 강한 모델을 계속 사용하거나, 반대로 프로젝트 전체 구조 분석을 낮은 Reasoning으로 시작하는 일이 생깁니다. 매번 사람이 모델을 고르는 것도 번거롭지만, 선택을 자동화하려고 별도 LLM 분류기를 호출하면 절약하려던 토큰을 Router가 먼저 소비합니다.

그래서 다음 원칙으로 설계했습니다.

1. 일반 요청은 Python 로컬 규칙만으로 판단한다.
2. 단일 키워드가 아니라 범위·동작·위험 신호의 조합을 본다.
3. 애매하면 `BALANCED + MEDIUM`으로 시작한다.
4. 실패 시 모델 변경보다 Reasoning 상승을 먼저 검토한다.
5. Provider 간 fallback은 허용하지 않는다.

## 동작 구조

```text
User Prompt
    │
    ▼
Local Rule Router ── Complexity Score + Confidence
    │                 (LLM/API 호출 없음)
    ▼
Provider-local Model Catalog
    │
    ▼
Recommendation ── 사용자 승인 [Y/n]
    │
    ▼
Codex CLI 또는 Claude Code 최초 실행
```

`router_config.json`의 규칙은 기본 점수 5(MEDIUM)에서 시작합니다. 좁은 문서·문구 수정은 점수를 낮추고, 프로젝트 전체 분석, 아키텍처 변경, 복잡한 디버깅, 다중 모듈, 테스트 요구는 조합이 성립할 때만 점수를 높입니다.

| Score | 결과 | 기본 모델 역할 |
|---:|---|---|
| 0–4 | LOW | FAST |
| 5–8 | MEDIUM | BALANCED |
| 9–14 | HIGH | STRONG |
| 15+ | XHIGH 검토 | STRONG/MAX 후보 |

위험한 DB migration, 운영 설정, 인증·권한, 보안, 대량 삭제는 짧은 요청이어도 HIGH floor를 적용합니다. 반대로 `README에 architecture라는 단어 한 줄 추가해줘`는 문맥상 좁은 문서 수정이므로 LOW입니다. Prompt 길이만으로 난이도를 올리지 않습니다.

## 주요 기능

- JSON 설정 기반 점수 규칙과 임계값
- 문맥 결합 조건(`all_of`, `any_of`, `none_of`)
- 낮은 Confidence의 보수적인 `BALANCED + MEDIUM` 기본값
- 요청별 독립 평가를 통한 자동 downgrade
- 현재 모델을 유지하면서 Reasoning을 우선 조정
- 모델별 지원 Effort에 맞춘 가장 가까운 단계 선택
- Weekly/Rate/Usage limit 상태 주입 및 같은 Provider 내 fallback
- 확인된 reset time만 출력하고, 미확인 값은 `Unknown` 처리
- 인증 오류와 모델 카탈로그 조회 실패 분리
- 선택 가능한 Debug Mode
- Router 자체의 OpenAI/Anthropic/기타 AI API 호출 없음

## 설치

요구사항:

- Python 3.11 이상
- 사용할 CLI: Codex CLI 또는 Claude Code

```powershell
git clone https://github.com/hongmin3/adaptive-model-router.git
cd adaptive-model-router
py -m pip install -e .
```

설치 후 어느 작업 디렉터리에서든 실행할 수 있습니다.

```powershell
route-ai "README에 설치 명령어 한 줄 추가해줘"
route-ai --provider claude "프로젝트 전체 구조를 분석하고 개선해줘"
```

저장소를 clone한 위치에서 설치 없이 시험하려면 다음 shim도 사용할 수 있습니다.

```powershell
.\route-codex.cmd --no-exec "README 오타 수정해줘"
.\route-claude.cmd --no-exec "일반 기능을 구현해줘"
```

## 사용 예시

```text
> route-ai "README에 architecture라는 단어 한 줄 추가해줘"

Codex

AVAILABLE

Recommended:
GPT-5.6-Luna

Reasoning:
LOW

Reason:
narrow simple edit

GPT-5.6-Luna / LOW로 실행할까요? [Y/n]
```

추천만 확인하고 실제 CLI를 실행하지 않으려면:

```powershell
route-ai --no-exec "이 프로젝트 전체 구조를 분석해줘"
```

## Debug Mode

기본값은 OFF입니다.

```powershell
route-ai --debug --no-exec "프로젝트 전체를 분석하고 아키텍처를 개선해줘"
```

```text
Router Debug

Score: 13

Matched:
+3 project-wide analysis
+3 architecture or refactoring work
+2 analysis and implementation

Result:
HIGH

Confidence:
0.92

Model:
STRONG
```

## 설정

모든 점수와 문맥 규칙은 [`router_config.json`](router_config.json)에 있습니다.

- `defaults`: 기본 점수, Confidence 기준, 불확실할 때의 결과
- `thresholds`: LOW/MEDIUM/HIGH 구간
- `rules`: 점수 조건과 위험도 floor
- `directives`: 사용자의 명시적 Reasoning/현재 모델 유지 요청
- `model_selection`: 모델 설명 기반 역할 분류와 Provider 내부 fallback 순서
- `providers`: CLI별 출력 용어와 런타임 모델 alias 해석
- `ai_fallback.enabled`: 기본값 `false`; 현재 구현은 LLM routing을 수행하지 않음

다른 설정 파일을 시험할 수도 있습니다.

```powershell
route-ai --config .\my_router_config.json --no-exec "요청"
```

## 모델과 한도 처리

기본 실행에서 Codex는 `~/.codex/models_cache.json`을 직접 읽고, Claude Code는 `router_config.json`의 Provider 전용 alias registry를 읽습니다. 이 과정에서는 Codex/Claude 프로세스도 실행하지 않습니다. 명시적으로 `--refresh-catalog`를 준 경우에만 `codex debug models` 또는 `claude --help`로 로컬 메타데이터를 갱신하며, 이 명령들도 모델 추론이나 토큰을 사용하지 않습니다.

계정별 사용량 상태를 CLI가 기계 판독 가능한 형태로 제공하지 않는 경우, 선택적으로 로컬 상태 파일을 전달할 수 있습니다.

```json
{
  "providers": {
    "codex": {
      "models": {
        "example-model": {
          "status": "WEEKLY_LIMIT_REACHED",
          "reset": "2026-09-18 09:00"
        }
      }
    }
  }
}
```

```powershell
route-ai --status-file .\model-status.json --no-exec "복잡한 버그를 분석해줘"
```

상태 파일은 실제 로컬/CLI 상태를 전달하기 위한 입력이며, Router가 reset 시간을 추측하지 않습니다.

## Escalation 정책

```text
현재 모델 / LOW
→ 현재 모델 / MEDIUM
→ 현재 모델 / HIGH
→ 더 강한 모델 / HIGH
→ 최상위 모델 / XHIGH
```

Syntax error, typo, 잘못된 명령어는 모델 한계로 간주하지 않습니다. Context가 큰 경우에도 먼저 검색, 관련 파일 우선 분석, 단계적 요약을 사용하고 곧바로 최고 모델로 올리지 않습니다.

## 테스트

외부 패키지 없이 표준 `unittest`로 실행됩니다.

```powershell
$env:PYTHONPATH = "$PWD\src"
py -m unittest discover -s tests -v
```

테스트에는 다음 경계 사례가 포함됩니다.

- 매우 단순한 요청과 일반 구현
- 일반/복잡한 버그
- 프로젝트 전체 구조 개선
- README 안의 `architecture` 단어 수정
- 짧지만 위험한 DB migration
- 길지만 단순한 문서 수정
- 모델 사용량 제한과 Provider 내부 fallback
- Reasoning 미지원 모델
- 애매한 요청의 MEDIUM 기본값

## 설계 경계

- Router는 실제 작업 내용을 수행하지 않습니다.
- 사용자가 승인하기 전에는 Codex나 Claude Code를 실행하지 않습니다.
- 한 Provider의 장애를 이유로 다른 Provider를 추천하거나 실행하지 않습니다.
- 확인할 수 없는 모델·한도·명령은 만들어내지 않고 `UNKNOWN` 또는 `Not verified`로 남깁니다.

## 기술 스택

- Python 3.11+
- Python Standard Library only
- JSON configuration
- unittest
- Codex CLI / Claude Code adapter

## License

MIT
