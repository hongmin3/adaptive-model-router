# Adaptive Model Router for Codex

> 모델 사용량을 줄이기 위한 Router가 매 요청마다 또 다른 모델을 호출해야 할까?

이 프로젝트는 제가 AI CLI를 사용하면서 느낀 모델 선택 비용을 해결하기 위해 기획하고 구현한 자동화입니다. 사용자는 평소처럼 `codex --yolo`를 실행하고 작업을 입력합니다. Codex가 실제 모델을 호출하기 전에 로컬 규칙 엔진이 난이도를 판정하고 적절한 모델과 Reasoning을 추천합니다.

```text
Recommended: GPT-X
Reasoning: HIGH

추천 모델로 바꾸시겠습니까? [Y/n]
```

`Y`를 입력하면 Codex가 같은 세션의 모델과 Reasoning을 자동 변경한 뒤 원래 Prompt를 실행합니다. `N`을 입력하면 현재 설정으로 원래 Prompt를 실행합니다. `/model`을 직접 열 필요가 없습니다.

## 한눈에 보는 성과

| 문제 | 구현 |
|---|---|
| Router 자체가 토큰을 소비함 | Python 로컬 규칙으로 분류하여 Router LLM 호출 0회 |
| 모든 작업에 같은 고성능 모델 사용 | 요청마다 독립적으로 LOW/MEDIUM/HIGH 재평가 |
| 단어 하나만 보고 난이도 오판 | 범위·행동·위험도·분석/구현 결합 조건 평가 |
| `/model` 수동 선택의 반복 | Codex TUI에 Y/N 승인과 세션 자동 전환을 최소 패치로 통합 |
| 공식 Codex 업데이트 유지보수 | 새 공식 릴리스 감지, 패치, 테스트, 빌드, Release 발행 자동화 |
| 업데이트 실패를 늦게 발견 | GitHub Actions 실패 시 유지보수 Issue 자동 생성 |

## 동작 구조

```text
User Prompt in codex --yolo
        │
        ▼
Patched Codex pre-submit gate
        │ stdin JSON
        ▼
Local Python Rule Router ── Complexity Score + Confidence
        │                    LLM/API/AI CLI 호출 없음
        ▼
Same-provider model recommendation
        │
        ▼
Y/N confirmation
  ├─ Y: model + reasoning 자동 적용 ─┐
  └─ N: 현재 설정 유지 ─────────────┤
                                     ▼
                           Original Prompt 실행
```

Router가 실패하면 Codex 자체는 계속 사용할 수 있도록 fail-open으로 설계했습니다. Slash command와 `!` shell command는 Router를 통과하지 않습니다.

## Complexity Score

규칙 원본은 [`router_config.json`](router_config.json)에 분리되어 있습니다. PC 설치 후 실제 사용자 설정은 `%LOCALAPPDATA%\AdaptiveModelRouter\router_config.json`에 최초 1회 복사되며, 업데이트 시 덮어쓰지 않습니다.

| 점수 | 분류 | 기본 역할 | Reasoning |
|---:|---|---|---|
| 0–4 | SIMPLE | FAST | LOW |
| 5–8 | NORMAL | BALANCED | MEDIUM |
| 9–14 | COMPLEX | STRONG | HIGH |
| 15+ | VERY COMPLEX | STRONG/MAX 검토 | XHIGH |

`README에 architecture라는 단어를 추가해줘`는 단순 문서 수정이므로 LOW입니다. `프로젝트 전체를 분석하고 architecture를 개선해줘`는 범위와 설계 변경이 결합되어 HIGH입니다. 짧더라도 DB migration, 운영 설정, 인증·권한, 보안, 대량 삭제는 HIGH floor가 적용됩니다. Prompt 길이 자체는 난이도 점수가 아닙니다.

애매한 요청은 최고 모델이 아니라 `BALANCED + MEDIUM`을 사용합니다. 실패 시에는 모델 교체보다 현재 모델의 Reasoning 상승을 먼저 검토합니다.

## 실제 PC에 적용하기

### 1. 저장소 Clone과 Router 설치

요구사항은 Windows 10/11, Python 3.11 이상, 로그인된 Codex CLI입니다.

```powershell
git clone https://github.com/hongmin3/adaptive-model-router.git
cd adaptive-model-router
py -m pip install .
```

### 2. 자동 빌드된 Codex 설치

최신 Windows 빌드를 내려받고 SHA-256을 검증한 후 `%LOCALAPPDATA%\AdaptiveModelRouter`에 설치합니다. 패치된 `codex.exe`뿐 아니라 공식 배포판과 같은 Code Mode host, Ripgrep, Windows sandbox helper도 함께 설치합니다.

```powershell
py .\scripts\manage_native_codex.py --install-latest
```

Installer는 해당 경로를 사용자 PATH의 앞에 배치합니다. 새 터미널에서 확인합니다.

```powershell
where.exe codex
codex --version
```

첫 경로가 `%LOCALAPPDATA%\AdaptiveModelRouter\bin\codex.exe`여야 합니다. 기존 npm Codex는 삭제하지 않으므로 PATH 우선순위만 되돌리면 원본으로 복귀할 수 있습니다.

### 3. PC 자동 업데이트 설치

매일 오전 10시에 이 프로젝트의 최신 검증 Release를 확인하고 새 버전이면 checksum 검증 후 자동 교체하는 Windows 작업을 등록합니다.

```powershell
py .\scripts\manage_native_codex.py --install-update-task
py .\scripts\manage_native_codex.py --status
```

작업 이름은 `AdaptiveModelRouterUpdate`입니다. 기존 실행 파일은 timestamp backup으로 보존됩니다.

### 4. 기존 임시 Hook 제거

Native 빌드를 설치했다면 중복 안내를 막기 위해 이전 Router Hook만 제거합니다. 다른 Hook은 유지됩니다.

```powershell
py .\scripts\manage_codex_hook.py --uninstall
```

### 5. 사용

```powershell
codex --yolo
```

일반 Prompt를 입력하면 추천이 표시됩니다.

```text
Adaptive Model Router

Recommended: GPT-X
Reasoning: HIGH
Profile: STRONG
Reason: project-wide analysis + architecture change

추천 모델로 바꾸시겠습니까? [Y/n]
```

- `Y`: 추천 모델/Reasoning으로 자동 변경하고 원래 요청 실행
- `N`: 현재 설정을 유지하고 원래 요청 실행
- 다른 입력: 작업을 실행하지 않고 Y/N을 다시 요청
- Router 비활성화: `ADAPTIVE_MODEL_ROUTER_ENABLED=0`

## 공식 Codex 업데이트 자동화

[`.github/workflows/upstream-codex-release.yml`](.github/workflows/upstream-codex-release.yml)이 매일 공식 `openai/codex` 최신 Release를 확인합니다.

```text
Official release 감지 → clean clone → patch check/apply
→ Python test + Rust build → Windows zip + SHA-256
→ Public GitHub Release → PC Scheduled Task 자동 설치
```

패치가 공식 코드 변경과 충돌하면 Release를 만들지 않고 `Codex upstream update needs patch maintenance` Issue를 자동 생성합니다. 잘못된 바이너리는 배포되지 않습니다. GitHub 저장소의 Actions/Issue 알림을 활성화하면 실패 알림도 받을 수 있습니다.

최초 설정자는 `Settings → Actions → General → Workflow permissions`에서 `Read and write permissions`를 허용해야 합니다. 이후 Actions 화면에서 `Build adaptive Codex on upstream release`를 한 번 실행하면 최초 Release가 생성됩니다.

세부 절차는 [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md)를 참고하세요.

## Debug Mode와 테스트

기본 Debug Mode는 OFF입니다. `%LOCALAPPDATA%\AdaptiveModelRouter\router_config.json`의 `hook.debug`를 `true`로 바꾸면 점수, Confidence, 매칭 규칙을 표시합니다. 이 출력도 로컬 템플릿이며 LLM을 사용하지 않습니다.

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
py -m unittest discover -s tests -v
```

테스트에는 문맥별 난이도, 위험 작업, 긴 단순 문서, 한도, Reasoning 미지원, 애매한 요청, zero-subprocess 경로, native JSON bridge, checksum 설치가 포함됩니다.

## 프로젝트 구조

```text
adaptive-model-router/
├── router_config.json
├── src/adaptive_model_router/
│   ├── scorer.py
│   ├── selector.py
│   ├── native_bridge.py
│   └── native_manager.py
├── codex-patches/adaptive-router.patch
├── .github/workflows/upstream-codex-release.yml
├── scripts/
└── tests/
```

## 설계 경계

- Codex에서는 OpenAI/Codex 모델만 선택합니다.
- Claude Code에서도 Claude 내부 모델만 선택하며 Provider 간 fallback은 없습니다.
- 확인할 수 없는 모델, 한도, reset time, 명령은 추측하지 않습니다.
- Router는 모델 선택을 위해 OpenAI API, Claude API, Codex 또는 Claude 프로세스를 실행하지 않습니다.
- 실제 모델 호출은 Y/N 결정 뒤 원래 작업이 제출될 때 처음 발생합니다.

## License

MIT
