# Adaptive Model Router for Codex

Codex 프롬프트를 로컬 규칙으로 분석해 작업 난이도에 맞는 모델 및 reasoning 강도를 추천하는 zero-token 라우터입니다. 모델 등급을 통해 비용을 간접적으로 고려합니다. 기본 경로에서는 LLM/API를 호출하지 않으며, 사용자가 확인한 경우에만 추천 모델과 reasoning을 현재 Codex 세션에 적용합니다.

## 현재 구현 상태

- 모델 후보: `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- 로컬 모델 카탈로그에 나타난 신규 모델을 설명과 설정된 slug 역할 패턴에 따라 후보로 반영
- `FAST`, `BALANCED`, `STRONG`, `MAX` 프로필에 따른 비용 등급 선택
- 프롬프트마다 `LOW`, `MEDIUM`, `HIGH`, `XHIGH` reasoning을 독립 결정
- 모델 능력 점수와 reasoning 점수를 분리해 최적 조합 선택
- 일괄 변경처럼 능력은 높고 추론은 가벼운 작업을 별도 처리
- 규칙 근거·위험도·경계·충돌을 이용한 동적 confidence 계산
- 근거가 없거나 confidence가 낮으면 Luna/LOW로 보수적 fallback
- 보안·운영·마이그레이션 등 고위험 작업은 reasoning floor 적용
- GPT/DeepSeek family 및 동일 프로필 대체 모델 표시
- 설명 문구가 바뀌어도 slug 패턴으로 Sol/Astra 역할 고정
- `previous-generation`, `deprecated` 모델 자동 제외
- AI second opinion은 아직 구현되지 않음

## 추천 알고리즘

1. `현재 모델 유지` 지시와 명시적 reasoning 지시를 반영합니다. 특정 모델 이름을 직접 지정하는 기능은 아직 없습니다.
2. 범위, 변경 규모, 위험도, 분석/구현 요구를 규칙으로 점수화합니다.
3. reasoning 점수와 model 점수를 분리합니다. 단순 작업은 Luna/LOW, 일반 작업은 Terra/MEDIUM, 전문 구현은 Sol/HIGH, 프로젝트 전체·고위험 작업은 Astra/XHIGH까지 올라갑니다.
4. 매칭이 있으면 confidence는 `0.32 + min(0.42, 규칙 수 × 0.14) + min(0.16, 최근접 점수 경계 거리 × 0.04) + 위험도 보정(0.10) - 상충 증거(0.15)`로 계산하고 `0.00~0.99`로 제한합니다. 매칭이 없으면 `0.15`입니다. 이 값은 통계적으로 보정된 성공 확률이 아니라 규칙 근거 강도입니다.
5. confidence가 `0.50` 미만이고 위험도 floor가 없으면 `FAST/LOW`로 낮춥니다. 범위가 명시된 큰 작업은 점수가 경계에 걸려 잘못 축소되지 않도록 별도 검증했습니다.
6. 모델 역할 키워드와 slug 패턴을 함께 평가해 카탈로그 설명 변경에도 안정적으로 선택합니다.

## 예시

| 프롬프트 유형 | 추천 |
|---|---|
| 모호한 요청 | GPT-5.6-Luna · LOW · confidence 0.15 |
| 여러 파일 일괄 이름 변경 | GPT-5.6-Sol · LOW |
| 프로젝트 전체 분석 | GPT-5.6-Sol · HIGH |
| 알고리즘 복잡도 분석 | GPT-5.6-Terra · HIGH |
| 복잡한 버그 분석·수정 | GPT-5.6-Sol · HIGH |
| 전체 아키텍처·보안·마이그레이션 | GPT-6-Astra · XHIGH |

## 실행 및 검증

```powershell
py -m pip install .
py .\scripts\manage_native_codex.py --install-latest
codex --yolo
```

`pip install`은 Python 라우터만 설치합니다. Codex TUI에서 제출 직전 Y/N 추천을 보려면 위의 패치된 Codex 설치가 별도로 필요합니다. 다른 `codex` 래퍼가 PATH 앞에 있다면 그 래퍼가 패치된 Codex를 실행하는지 확인하세요. 추천 화면에서 `Y`는 추천 적용 후 실행, `N`은 현재 설정 유지입니다. 비활성화:

```powershell
$env:ADAPTIVE_MODEL_ROUTER_ENABLED = "0"
```

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
$env:ADAPTIVE_MODEL_ROUTER_CONFIG = "$PWD\router_config.json"
py -m unittest discover -s tests -v
```

현재 회귀 묶음은 한국어·영어와 단순 편집, 구현, 진단, 광범위 분석, 다중 파일 작업, 위험 작업, 문자열 인용을 포함한 **71개 프롬프트**를 검사합니다. 전체 테스트는 **42개**입니다.

## 설정

- 저장소 기본값: [`router_config.json`](router_config.json)
- 설치 후 설정: `%LOCALAPPDATA%\AdaptiveModelRouter\router_config.json`
- `rules`에서 키워드·점수·위험도·충돌 여부 조정
- `model_selection.slug_role_patterns`에 신규 모델 역할 패턴 추가

## 제한사항

Codex provider 범위에서만 선택하며 provider 자체는 자동 전환하지 않습니다. 실제 호출은 Y/N 확인 뒤 Codex가 수행합니다. 후보 모델은 로컬 캐시에 있어야 하며, 알려지지 않은 새 모델의 능력과 가격을 자동 검증하지는 못합니다. 실제 토큰 사용량·요금·성공률을 수집하거나 최적화하지 않으므로 비용 선택은 모델 등급에 따른 휴리스틱입니다. AI second opinion은 아직 구현되지 않았습니다.

## License

MIT
