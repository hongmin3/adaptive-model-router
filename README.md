# Adaptive Model Router

Prompt를 로컬 규칙으로 분석해 작업 난이도에 맞는 모델과 reasoning 강도를 추천하는 zero-token 라우터입니다. 모델 등급을 통해 비용을 간접적으로 고려합니다. 추천 단계에서는 LLM/API를 호출하지 않으며, 사용자가 확인한 경우에만 추천 설정이 실제 세션에 적용됩니다.

**Codex CLI**와 **Claude Code CLI**를 모두 지원하며, 두 CLI는 서로 독립적으로 동작합니다.

| | Codex | Claude Code |
|---|---|---|
| 적용 방식 | 패치된 `codex.exe` (TUI에 Y/N 화면 삽입) | 공식 `UserPromptSubmit` Hook |
| 설치 필요 | `pip install` + 패치 바이너리 | `pip install` + Hook 등록 (바이너리 패치 불필요) |
| 모델 후보 | `~/.codex/models_cache.json` 등 로컬 카탈로그 | Claude Code 빌드의 tier 표 |
| 현재 모델 읽는 곳 | `~/.codex/config.toml` | `~/.claude/settings.json` |
| 승인 상태 저장 | `~/.codex/adaptive-model-router/state` | `~/.claude/adaptive-model-router/state` |

공유하는 것은 Python 패키지와 `router_config.json`(점수 규칙) 뿐입니다. **선택은 언제나 현재 실행 중인 provider 안에서만 이뤄지며, provider를 자동으로 바꾸지 않습니다.** 한쪽만 설치해도 되고, 둘 다 설치해도 서로 간섭하지 않습니다.

## 현재 구현 상태

- 모델 후보: `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- 로컬 모델 카탈로그에 나타난 신규 모델을 설명과 설정된 slug 역할 패턴에 따라 후보로 반영
- `FAST`, `BALANCED`, `STRONG`, `MAX` 프로필에 따른 비용 등급 선택
- 프롬프트마다 `LOW`, `MEDIUM`, `HIGH`, `XHIGH` reasoning을 독립 결정하고, 사용자가 명시하면 `MAX`/`ULTRA`까지 지정 가능
- 프로필에 해당하는 모델이 설정에 없으면 카탈로그 나열 순서로 임의 선택하지 않고 `no-evidence`로 표시 (`--debug`의 `Selection Evidence`)
- 모델 능력 점수와 reasoning 점수를 분리해 최적 조합 선택
- 일괄 변경처럼 능력은 높고 추론은 가벼운 작업을 별도 처리
- 규칙 근거·위험도·경계·충돌을 이용한 동적 confidence 계산
- 근거가 없거나 confidence가 낮으면 Luna/LOW로 보수적 fallback
- 보안·운영·마이그레이션 등 고위험 작업은 reasoning floor 적용
- GPT/DeepSeek family 및 동일 프로필 대체 모델 표시
- 설명 문구가 바뀌어도 slug 패턴으로 Sol/Astra 역할 고정
- `previous-generation`, `deprecated` 모델 자동 제외
- Claude Code CLI의 공식 `UserPromptSubmit` Hook에 등록해 Prompt 제출 시 동일하게 자동 적용 (아래 [Claude Code CLI 자동 적용](#claude-code-cli-자동-적용) 참고)
- AI second opinion은 아직 구현되지 않음

## 추천 알고리즘

1. `현재 모델 유지` 지시와 명시적 reasoning 지시를 반영합니다. 특정 모델 이름을 직접 지정하는 기능은 아직 없습니다.
2. 범위, 변경 규모, 위험도, 분석/구현 요구를 규칙으로 점수화합니다.
3. reasoning 점수와 model 점수를 분리합니다. 단순 작업은 Luna/LOW, 일반 작업은 Terra/MEDIUM, 전문 구현은 Sol/HIGH, 프로젝트 전체·고위험 작업은 Astra/XHIGH까지 올라갑니다.
4. 매칭이 있으면 confidence는 `0.32 + min(0.42, 규칙 수 × 0.14) + min(0.16, 최근접 점수 경계 거리 × 0.04) + 위험도 보정(0.10) - 상충 증거(0.15)`로 계산하고 `0.00~0.99`로 제한합니다. 매칭이 없으면 `0.15`입니다. 이 값은 통계적으로 보정된 성공 확률이 아니라 규칙 근거 강도입니다.
5. confidence가 `0.50` 미만이고 위험도 floor가 없으면 `FAST/LOW`로 낮춥니다. 범위가 명시된 큰 작업은 점수가 경계에 걸려 잘못 축소되지 않도록 별도 검증했습니다.
6. 모델 역할 키워드와 slug 패턴을 함께 평가해 카탈로그 설명 변경에도 안정적으로 선택합니다.
7. 프로필에 해당하는 모델을 설정 어디에서도 찾지 못하면 카탈로그의 나열 순서(`priority`)로 임의 선택하지 않고 그 사실을 `no-evidence`로 기록합니다. `priority`는 표시 순서일 뿐 성능 정보가 아니므로, 그대로 쓰면 가장 어려운 작업이 가장 싼 모델로 가는 식의 역전이 조용히 발생합니다.

## Claude 모델·Reasoning 선정 근거

Router는 Claude 모델 정보를 사용자에게 입력받지 않고, 학습된 지식이나 외부 API로도 가져오지 않습니다. 아래 세 가지 로컬 출처만 사용합니다.

| # | 출처 | 역할 | 갱신 시점 |
|---|---|---|---|
| 1 | `router_config.json` → `providers.claude.verified_alias_families` | Router가 분류해 둔 tier 표 | 사람이 직접 관리 |
| 2 | `providers.claude.families.anthropic.profile_models` | 프로필 ↔ alias 고정 매핑. **실제 선택을 결정하는 값** | 사람이 직접 관리 |
| 3 | **Claude Code 실행 파일 안의 tier 표** | 설치된 빌드가 아는 **전체** tier 목록, 각 tier의 설명, 각 alias가 가리키는 실제 모델 ID | 빌드가 바뀌면 자동 재수집 |
| 4 | `claude --help` | effort 사다리(`low`~`max`)와 alias 예시 확인 | 실행 시마다 |

4번이 1번을 대체하지 않는 이유: `--help`의 모델 설명은 `Provide an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet')`처럼 **예시**만 나열하며 전체 목록이 아닙니다. 예전 구현은 이 예시로 tier 표를 통째로 덮어써서 예시에 없는 `haiku`가 사라졌고, 그 결과 FAST 프로필이 가장 비싼 모델로 넘어갔습니다.

### 신규 모델 자동 반영

Claude Code는 기계 판독 가능한 모델 카탈로그도, `models` 서브커맨드도 제공하지 않습니다. 대신 실행 파일 자체가 `/model` 선택 화면을 그릴 때 쓰는 tier 표를 들고 있어서, Router가 그것을 직접 읽습니다(220MB 바이너리 스트리밍 스캔, 약 0.35초). 결과는 실행 파일의 경로·크기·수정시각을 키로 캐시되므로 Claude Code를 업데이트했을 때만 다시 읽습니다.

여기서 **버전**과 **등급**은 다르게 취급됩니다.

- **새 버전 (Opus 5 → Opus 6): 아무것도 안 해도 됩니다.** Claude Code의 `--model`은 `opus` 같은 *tier alias*를 받고, alias는 언제나 그 등급의 최신 모델을 가리킵니다. Router는 alias를 그대로 넘기므로 Claude Code만 업데이트하면 자동으로 새 버전이 쓰입니다. `--list-models`의 Version 열이 바뀐 걸로 확인할 수 있습니다. (Codex는 반대로 `gpt-5.6-sol`처럼 버전이 박힌 slug를 직접 노출하기 때문에 `slug_role_patterns`가 필요합니다.)
- **새 등급 (`fable`이 처음 생겼을 때 같은 경우): 자동 감지, 배치는 확인 1회.** Router가 빌드에서 새 tier를 발견하면 목록 맨 뒤에 넣고 `--list-models`와 카탈로그 message에 이름을 띄웁니다. 하지만 **어느 프로필에 넣을지는 추측하지 않습니다.**

등급을 자동 추측하지 않는 이유는 분명합니다. 빌드가 주는 정보는 이름과 한 줄 설명뿐이고, 설명만으로는 기존 등급과의 상대적 위치를 알 수 없습니다 — `fable`의 "For your toughest challenges"와 `opus`의 "Most capable for ambitious work" 중 어느 쪽이 위인지 키워드 규칙으로는 판정할 수 없습니다. 그리고 순위를 잘못 넣으면 **아무것도 실패하지 않은 채** 가장 어려운 작업이 가장 싼 모델로 갑니다. 이 프로젝트에서 실제로 났던 버그가 정확히 그것입니다. 그래서 분류되지 않은 tier는 어떤 프로필의 1순위도 되지 못하게 격리하고, 사람이 한 번 지정하면 그때부터 라우팅에 참여합니다.

빌드가 이름만 알고 기본 모델 ID가 없는 tier(예: 제한 접근 프로그램인 `mythos`)는 `--model`로 해석되지 않으므로 후보에 넣지 않고 `not selectable`로만 표시합니다.

### Tier 표

| 프로필 | `--model` alias | Claude Code가 자체 표기하는 tier 설명 |
|---|---|---|
| `FAST` | `haiku` | Fastest for quick answers |
| `BALANCED` | `sonnet` | Most efficient for everyday tasks |
| `STRONG` | `opus` | Most capable for ambitious work |
| `MAX` | `fable` | For your toughest challenges |

설명 문구는 CLI 표기와 맞춰 두었지만, **선택을 결정하는 것은 문구가 아니라 pin**입니다. 문구는 이웃 등급과 겹칩니다 — `opus`의 "Most capable"은 `MAX` 힌트와 그대로 충돌하므로, 문구가 이기면 MAX 요청이 `opus`에서 멈춥니다. 어떤 family가 모델을 pin 해 두면 그 모델에 대해서는 pin이 최종 답이고 문구는 보지 않습니다.

### 모델 선정 순서

1. 사용자가 `현재 모델 유지`를 지시했으면 현재 모델을 그대로 씁니다 (`forced-current`).
2. 현재 모델이 이미 그 프로필에 pin 되어 있으면 유지합니다 (`kept-current`).
3. 그 외에는 프로필의 pin → slug 패턴 → 설명 키워드 순으로 후보를 정렬해 1순위를 고릅니다 (`pinned` / `slug-role` / `description-hint`).
4. 1순위가 한도 초과·사용 불가면 `fallback_profiles` 순서로 같은 provider 안에서만 대체합니다 (`fallback-profile`). Provider 자체는 절대 전환하지 않습니다.
5. 어느 단계에서도 근거를 찾지 못하면 `no-evidence`로 표시합니다. `--debug`의 `Selection Evidence` 줄에서 확인할 수 있습니다.

### Effort(Reasoning) 선정 순서

Claude Code가 받는 effort는 `low`, `medium`, `high`, `xhigh`, `max` 다섯 단계입니다 (`claude --help`의 `--effort <level>` 줄에서 확인하며, `--refresh-catalog`를 쓰면 실행 시점 값을 그대로 반영합니다). Codex는 여기에 `ultra`가 더 있습니다.

1. 규칙 점수를 `low_max`/`medium_max`/`high_max` 임계값에 대어 `LOW`/`MEDIUM`/`HIGH`/`XHIGH`를 정합니다.
2. 고위험 규칙(`risk_floor`)이 걸리면 그 아래로 내려가지 않습니다.
3. confidence가 임계값 미만이고 위험 floor도 없으면 `LOW`로 낮춥니다.
4. 프롬프트에 명시적 지시(`reasoning high`, `effort max`, `사용량 아껴` 등)가 있으면 위 결과를 덮어씁니다. 지정 가능한 값은 `LOW`~`XHIGH`에 더해 `MAX`, `ULTRA`입니다.
5. 고른 모델이 그 단계를 지원하지 않으면 가장 가까운 단계로 조정하고(동률이면 올림) 그 사실을 문구로 표시합니다. Claude에는 `ultra`가 없으므로 `ULTRA` 지시는 `max`로 내려갑니다.

### 현재 모델과 Effort는 어디까지 알 수 있나

Claude Code 2.1.274에서 실제 Hook 호출을 캡처해 확인한 결과입니다.

| | 얻을 수 있나 | 출처 |
|---|---|---|
| 현재 **Effort** | **가능** | `CLAUDE_EFFORT` 환경변수 (`max` 등). Claude Code가 모든 Hook 명령에 내려줍니다 |
| 현재 **모델** | 불가능 | Hook 입력 JSON에도, 환경변수에도 없습니다 |

`UserPromptSubmit`의 payload 키는 `session_id`, `transcript_path`, `cwd`, `prompt`, `prompt_id`, `permission_mode`, `scratchpad_dir`, `hook_event_name`이 전부입니다. `effort`는 tool-use 컨텍스트 Hook(`PreToolUse` 등)에만 들어가고 `UserPromptSubmit`에는 없지만, 같은 값이 `CLAUDE_EFFORT`로 노출되므로 Router는 그쪽에서 읽습니다.

모델은 사용자가 `~/.claude/settings.json`에 `"model"`을 고정해 둔 경우에만 표시되고, 그렇지 않으면 `Current Model: UNKNOWN`입니다. 이건 데스크톱 앱만의 제약이 아니라 터미널에서도 같습니다.

`payload`에 `source` 필드도 없습니다(2.1.274 실측). Router의 `source != "user"` 통과 규칙은 현재 발동하지 않으며, 예약 실행·SDK 호출은 위의 entrypoint 필터가 대신 걸러냅니다.

### 이 방식의 한계

- tier 표 추출은 Claude Code 번들의 내부 식별자(`ANTHROPIC_TIER_NAMES`, `TIER_DESCRIPTIONS`)에 의존합니다. Anthropic이 이 구조를 바꾸면 추출이 실패하는데, 그때는 조용히 틀리는 대신 설정값만 쓰고 `--list-models`가 "Tier 표를 읽지 못했습니다"라고 알립니다.
- 새 등급이 감지되면 이름·설명·버전만 알려주고 등급 분류는 하지 않습니다. 분류 전까지 그 모델은 어떤 프로필의 1순위도 되지 않습니다.
- 가격·성능을 실측하지 않으므로 등급은 어디까지나 휴리스틱입니다.

## 예시

| 프롬프트 유형 | Codex 추천 | Claude Code 추천 |
|---|---|---|
| 모호한 요청 | GPT-5.6-Luna · LOW · confidence 0.15 | haiku · LOW |
| 여러 파일 일괄 이름 변경 | GPT-5.6-Sol · LOW | opus · LOW |
| 프로젝트 전체 분석 | GPT-5.6-Sol · HIGH | opus · HIGH |
| 알고리즘 복잡도 분석 | GPT-5.6-Terra · HIGH | sonnet · HIGH |
| 복잡한 버그 분석·수정 | GPT-5.6-Sol · HIGH | opus · HIGH |
| 전체 아키텍처·보안·마이그레이션 | GPT-6-Astra · XHIGH | fable · XHIGH |
| `reasoning ultra`를 명시한 전체 분석 | GPT-5.6-Sol · ULTRA | opus · MAX (ultra 미지원, 최근접 조정) |

## 설치

### 0. 공통 — Python 패키지

두 provider 모두 이 단계가 먼저입니다. 저장소 루트에서 실행하세요.

```powershell
py -m pip install .
```

> **이미 설치한 적이 있다면 반드시 다시 실행하세요.** Hook과 CLI는 저장소의 `src/`가 아니라 **설치된 사본**을 실행합니다. 재설치를 건너뛰면 예전 코드가 그대로 돌고, 아무 오류 없이 옛 규칙으로 라우팅됩니다.

설치한 Python이 여러 개라면, Hook 등록에 쓰는 것과 같은 것으로 설치해야 합니다(`manage_claude_hook.py`가 실행 시점의 `sys.executable`을 Hook 명령에 적어 넣습니다).

### 1. 설정 파일 확인

설정은 `%LOCALAPPDATA%\AdaptiveModelRouter\router_config.json`에 한 번 만들어지고 이후 덮어쓰이지 않습니다. 예전에 설치한 적이 있다면 버전이 뒤처져 있을 수 있습니다.

```powershell
py -m adaptive_model_router.cli --provider claude --list-models
```

출력 첫 줄이 `Config: ... (version N)`이고, 패키지 기본값보다 낮으면 경고가 뜹니다. 그때만:

```powershell
py -m adaptive_model_router.cli --refresh-config
```

기존 파일은 `%LOCALAPPDATA%\AdaptiveModelRouter\backups\<타임스탬프>\`에 백업된 뒤 교체됩니다. 직접 수정한 규칙이 있다면 백업본과 비교해 다시 반영하세요.

### 2-A. Claude Code CLI 설정

패치된 바이너리가 필요 없습니다. 공식 `UserPromptSubmit` Hook에 등록만 하면 됩니다.

```powershell
py .\scripts\manage_claude_hook.py --install
py .\scripts\manage_claude_hook.py --status      # INSTALLED / NOT INSTALLED
py .\scripts\manage_claude_hook.py --uninstall
```

`~/.claude/settings.json`의 `hooks.UserPromptSubmit`에만 항목을 추가하고, 테마·플러그인·다른 Hook 등 나머지 설정은 그대로 둡니다. 기존 파일은 `%USERPROFILE%\.claude\adaptive-model-router\backups\<타임스탬프>\`에 백업됩니다. 같은 항목이 이미 있으면 아무것도 하지 않습니다(멱등).

등록되는 내용은 다음 한 줄입니다.

```json
{ "type": "command",
  "command": "\"<python.exe>\" -m adaptive_model_router.hook --provider claude",
  "timeout": 10 }
```

**적용은 다음 세션부터입니다.** 이미 열려 있는 Claude Code 세션에는 반영되지 않으니 새로 시작하거나 `/resume` 하세요.

### 2-B. Codex CLI 설정

Codex는 TUI에 Y/N 화면을 넣기 위해 패치된 바이너리가 필요합니다.

```powershell
py .\scripts\manage_native_codex.py --install-latest
py .\scripts\manage_native_codex.py --status
codex --yolo
```

다른 `codex` 래퍼가 PATH 앞에 있다면, 그 래퍼가 패치된 Codex를 실행하는지 확인하세요. 일시 비활성화:

```powershell
$env:ADAPTIVE_MODEL_ROUTER_ENABLED = "0"
```

## 사용 방법

### 어느 화면에서 동작하는가 — 터미널에서만

`~/.claude/settings.json`은 **사용자 단위**라 Hook을 한 번 등록하면 Claude Code의 모든 화면(터미널, 데스크톱 앱, VS Code 확장, SDK)에서 발동합니다. 하지만 "같은 Prompt를 다시 제출해 승인"할 사람이 있는 건 터미널 세션뿐이고, GUI에서 매번 차단 화면이 뜨면 방해만 됩니다.

그래서 Router는 실행 화면을 보고 터미널일 때만 개입합니다. 판정에 쓰는 것은 Claude Code가 하위 프로세스에 내려주는 `CLAUDE_CODE_ENTRYPOINT` 환경변수입니다.

| 값 | Router |
|---|---|
| 미설정 (터미널 `claude` 세션), `cli`, `ssh-remote`, `claude-coworker-terminal` | **동작** |
| `claude-desktop`, `claude-desktop-3p`, `remote_desktop`, `remote_mobile` | 통과 |
| `claude-vscode`, `sdk-cli`, `sdk-ts`, `sdk-py`, `local-agent`, `mcp` | 통과 |
| `claude_in_slack`, `claude-in-teams`, 그 밖의 새로 생기는 값 | 통과 |

**deny-list가 아니라 allow-list입니다.** 앞으로 Anthropic이 새 화면을 추가해도 목록에 없으면 그냥 통과하므로, 모르는 GUI에서 갑자기 차단 화면이 뜨는 일은 없습니다. 반대 방향(새 터미널 화면이 생겼는데 조용히 동작 안 함)은 설정 한 줄로 해결합니다.

```json
"hook": { "claude_entrypoints": ["cli", "ssh-remote", "claude-coworker-terminal"] }
```

데스크톱 앱에서도 쓰고 싶으면 `"claude-desktop"`을 추가하고, 반대로 완전히 끄려면 `[]`가 아니라 존재하지 않는 값 하나를 넣으세요(빈 목록은 "필터 없음"으로 해석됩니다).

지금 쓰는 화면이 무엇으로 보고되는지 확인:

```powershell
echo $env:CLAUDE_CODE_ENTRYPOINT
```

> **Codex는 이 설정과 무관합니다.** Codex 쪽은 패치된 터미널 TUI(`codex-rs/tui/`)에만 들어가므로 ChatGPT 데스크톱 앱에서는 애초에 실행되지 않습니다.

### Claude Code에서 — Prompt를 그냥 평소처럼 쓰면 됩니다

Prompt를 제출하면 Router가 먼저 가로채 추천을 띄우고 **그 한 번은 실행을 막습니다.**

```
Adaptive Model Router

MODEL CALL BLOCKED BEFORE EXECUTION

Current Model: UNKNOWN (pin one in settings.json to show it here)
Current Effort: MAX

Recommended: fable
Effort: XHIGH
Reason: project-wide analysis, high-impact operation

승인 방법:
1. 추천 설정을 쓰려면 /model에서 위 모델과 Effort 값을 선택하세요.
2. 현재 설정을 유지하려면 변경하지 않아도 됩니다.
3. 같은 Prompt를 다시 제출하면 승인으로 간주되어 실제 작업이 시작됩니다.

Router 판단에는 LLM/API 호출과 모델 토큰이 사용되지 않았습니다.
```

여기서 할 수 있는 선택은 셋입니다.

| 원하는 것 | 할 일 |
|---|---|
| 추천대로 가기 | `/model`에서 추천 모델·Effort로 바꾼 뒤 **같은 Prompt를 다시 제출** |
| 지금 설정 그대로 가기 | 아무것도 바꾸지 말고 **같은 Prompt를 다시 제출** |
| 이번 건 취소 | 다른 Prompt를 쓰면 됩니다 (승인은 Prompt 단위) |

재제출이 곧 승인입니다. 승인은 **세션 ID + Prompt 내용 해시**로 저장되고 기본 **10분**(`hook.approval_ttl_seconds`) 뒤 만료되므로, 내용이 한 글자라도 다르면 다시 추천이 뜹니다. 저장되는 것은 해시·추천값·점수뿐이고 **Prompt 본문은 저장하지 않습니다.**

자동으로 통과되는 경우:

- `source`가 `user`가 아닌 Prompt — 예약된 wakeup, SDK/Subagent 호출 등. 승인할 사람이 없기 때문입니다.
- Router가 예외를 던진 경우 — `{"continue": true}`로 fail-open 하므로 Claude Code가 막히지 않습니다.

### Codex에서

패치된 TUI가 제출 직전에 추천 화면을 띄웁니다. `Y`는 추천 적용 후 실행, `N`은 현재 설정 유지입니다.

### CLI로 직접 — 설치 없이/설치 후 모두

```powershell
py -m adaptive_model_router.cli --provider claude --no-exec --debug "프로젝트 전체를 분석해줘"
```

```
Claude Code

AVAILABLE

Recommended:
opus

Effort:
HIGH

Reason:
project-wide analysis

Router Debug

Score: 9

Model Score: 9

Matched:
+4 project-wide analysis

Result:
HIGH

Confidence:
0.50

Confidence Basis:
evidence=0.14 (1 rule match); margin=0.04; risk=0.00; conflict=-0.00

Model:
STRONG

Selection Evidence:
pinned
```

`Selection Evidence`가 `no-evidence`면, 그 프로필에 해당하는 모델이 설정에 없어서 카탈로그 나열 순서로 고른 것입니다. 설정을 손봐야 한다는 신호입니다.

`--no-exec`를 빼면 확인 후 실제로 CLI를 실행합니다(`--yes`로 확인 생략).

## 프롬프트로 Router에 직접 지시하기

Prompt 안에 아래 표현이 들어 있으면 점수 계산 결과를 덮어씁니다. `--debug`의 `Reason`이 `explicit user setting`으로 바뀝니다.

| 지시 | 표현 (일부만 있어도 인식) |
|---|---|
| 모델 유지 | `이 모델 그대로`, `현재 모델 유지`, `모델 바꾸지 마`, `keep current model` |
| Effort LOW | `reasoning low`, `low로 해`, `사용량 아껴`, `빠르게 해` |
| Effort MEDIUM | `reasoning medium`, `medium으로 해`, `medium으로 실행` |
| Effort HIGH | `reasoning high`, `high로 해`, `정확도가 제일 중요` |
| Effort XHIGH | `reasoning xhigh`, `xhigh로 해` |
| Effort MAX | `reasoning max`, `effort max`, `max로 해`, `최고 성능으로` |
| Effort ULTRA | `reasoning ultra`, `effort ultra`, `ultra로 해` (Codex 전용, Claude에서는 `max`로 조정) |

예: `사용량 아껴서 프로젝트 전체를 분석해줘` → 모델은 범위에 맞춰 `opus`, Effort만 `LOW`.

여러 지시가 겹치면 설정 파일에 나중에 적힌 쪽이 이깁니다. 모델을 이름으로 직접 지정하는 기능은 아직 없습니다.

## 명령 레퍼런스

```powershell
py -m adaptive_model_router.cli [옵션] "프롬프트"
```

| 옵션 | 설명 |
|---|---|
| `--provider codex\|claude` | 대상 CLI. 기본값 `codex` |
| `--no-exec` | 추천만 출력하고 실행하지 않음 |
| `--yes` | 확인 없이 바로 실행 |
| `--debug` | 점수·매칭 규칙·confidence·선택 근거 출력 |
| `--list-models` | 모델 목록, 각 모델이 가리키는 버전, 배정된 프로필 출력 |
| `--refresh-catalog` | CLI 메타데이터를 실행 시점에 다시 조회 (기본은 로컬 파일만) |
| `--refresh-config` | 설치된 사용자 설정을 패키지 기본값으로 교체 (백업 후) |
| `--config <경로>` | 다른 설정 파일 사용 |
| `--status-file <경로>` | 모델 사용 가능 여부를 로컬 JSON으로 전달 |
| `--family <이름>` | 라우팅할 모델 family 지정 (Codex: `openai`, `deepseek`) |
| `--claude-command` / `--codex-command` | 실행 파일 이름·경로 지정 |

```powershell
py .\scripts\manage_claude_hook.py --install | --status | --uninstall
py .\scripts\manage_native_codex.py --install-latest | --status | --install-update-task
```

## 점검과 문제 해결

| 증상 | 확인 |
|---|---|
| Hook이 아무 반응이 없다 | `manage_claude_hook.py --status`로 등록 확인 → **새 세션에서** 다시 시도. 이미 열린 세션에는 적용되지 않습니다 |
| 추천이 예전 규칙대로 나온다 | `py -m pip install .`을 다시 실행했는지 확인. Hook은 설치된 사본을 실행합니다 |
| `Reason`이 늘 `no matching routing evidence` | `--list-models` 첫 줄의 설정 버전 확인 → 낮으면 `--refresh-config` |
| Profile 칸이 `-` 로 나온다 | 그 모델은 아직 어느 등급에도 배정되지 않았습니다. 아래 **설정** 참고 |
| 모델 목록이 `UNKNOWN` | 해당 CLI의 로컬 카탈로그를 읽지 못한 상태입니다. `codex --version` / `claude --version`으로 CLI 존재부터 확인 |
| Claude Code가 느려졌다 | Hook `timeout`은 10초이고 기본 경로는 subprocess를 실행하지 않습니다. 그래도 의심되면 `--uninstall` 후 비교 |
| 데스크톱 앱/VS Code에서 차단 화면이 뜬다 | `hook.claude_entrypoints`에 그 화면 값이 들어 있는지 확인. 기본값은 터미널만 허용합니다 |
| 터미널인데 Router가 안 뜬다 | `echo $env:CLAUDE_CODE_ENTRYPOINT`로 값을 확인하고 `hook.claude_entrypoints`에 추가 |

회귀 테스트:

```powershell
py .\run_tests.py -v
```

`py -m unittest discover -s tests`로 직접 돌리지 마세요. 그 형태는 `adaptive_model_router`를 `sys.path`에서 찾으므로, 이미 설치된 사본이 있으면 작업 중인 `src/`가 아니라 **그 설치본이 검사 대상**이 됩니다. 그 상태에서는 새로 추가한 테스트만 실패하고 나머지는 통과해서, 빌드가 잘못됐다는 사실이 "새 테스트가 잘못됐다"처럼 보입니다. `run_tests.py`는 `src/`를 앞에 넣고, `tests/test_environment.py`가 어떤 경로로 실행하든 이 조건을 다시 검사해 어긋나면 실행 방법까지 알려주며 실패합니다.

현재 회귀 묶음은 한국어·영어와 단순 편집, 구현, 진단, 광범위 분석, 다중 파일 작업, 위험 작업, 문자열 인용, 명시적 effort 지시를 포함한 **100개 프롬프트**를 검사합니다. 그중 29개는 프로필·reasoning에서 멈추지 않고 실제 Claude alias와 effort까지 확인하며, 프로필 4종 × reasoning 6단계를 모두 덮는지도 테스트가 스스로 검사합니다. 전체 테스트는 **115개**이고, 실행 파일 tier 추출은 실제 222MB 바이너리 대신 합성 fixture로 검사하므로 결과가 이 PC의 설치 상태에 좌우되지 않습니다.

## 제거

```powershell
py .\scripts\manage_claude_hook.py --uninstall     # Claude Code Hook만 제거
py -m pip uninstall adaptive-model-router          # Python 패키지 제거
```

설정(`%LOCALAPPDATA%\AdaptiveModelRouter\`)과 승인 상태(`~/.claude/adaptive-model-router/`)는 남습니다. 필요하면 직접 지우세요.

## 설정

- 저장소 기본값: [`router_config.json`](router_config.json)
- 설치 후 설정: `%LOCALAPPDATA%\AdaptiveModelRouter\router_config.json`
- `hook.claude_entrypoints`에서 Router가 동작할 Claude Code 화면 지정 (기본: 터미널만)
- 설치된 사용자 설정은 한 번 만들어지면 갱신되지 않습니다. 이후 버전에서 추가된 **dict** 항목(모델 등급, 프로필 pin, directive)은 병합되어 전달되지만 **list** 항목(`rules`)은 통째로 사용자 답이라 병합되지 않습니다. `--list-models`가 버전 차이를 감지해 경고하며, `py -m adaptive_model_router.cli --refresh-config`로 백업 후 교체할 수 있습니다
- `rules`에서 키워드·점수·위험도·충돌 여부 조정
- `model_selection.slug_role_patterns`에 신규 모델 역할 패턴 추가
- `providers.<provider>.families.<family>.profile_models`에 프로필별 모델 pin 추가. 새 모델을 어느 등급으로 쓸지 정하는 곳은 여기입니다
- Claude 모델을 추가·교체할 때는 `providers.claude.verified_alias_families`와 위 `profile_models`를 **함께** 고치세요. 둘이 어긋나면 `tests/test_claude_matrix.py`가 실패합니다

## 제한사항

현재 실행 중인 provider(Codex 또는 Claude Code) 안에서만 선택하며 provider 자체는 자동 전환하지 않습니다. 실제 호출은 사용자가 확인한 뒤 해당 CLI가 수행합니다. 후보 모델은 로컬 캐시에 있어야 하며, 알려지지 않은 새 모델의 능력과 가격을 자동 검증하지는 못합니다. 실제 토큰 사용량·요금·성공률을 수집하거나 최적화하지 않으므로 비용 선택은 모델 등급에 따른 휴리스틱입니다. AI second opinion은 아직 구현되지 않았습니다.

## License

MIT
