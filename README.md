# Adaptive Model Router for Codex

Codex 프롬프트를 로컬 규칙으로 분석해 비용과 난이도에 맞는 모델 및 reasoning 강도를 추천하는 zero-token 라우터입니다. 기본 경로에서는 LLM/API를 호출하지 않으며, 사용자가 확인한 경우에만 추천 모델과 reasoning을 현재 Codex 세션에 적용합니다.

## 현재 구현 상태

- 모델 후보: `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- 로컬 모델 카탈로그와 slug 역할 패턴으로 신규 모델을 자동 분류
- `FAST`, `BALANCED`, `STRONG`, `MAX` 프로필과 비용-aware 모델 선택
- 프롬프트마다 `LOW`, `MEDIUM`, `HIGH`, `XHIGH` reasoning을 독립 결정
- 모델 능력 점수와 reasoning 점수를 분리해 최적 조합 선택
- 일괄 변경처럼 능력은 높고 추론은 가벼운 작업을 별도 처리
- 규칙 근거·위험도·경계·충돌을 이용한 동적 confidence 계산
- 근거가 없거나 confidence가 낮으면 Luna/LOW로 보수적 fallback
- 보안·운영·마이그레이션 등 고위험 작업은 reasoning floor 적용
- GPT/DeepSeek family 및 동일 프로필 대체 모델 표시
- 설명 문구가 바뀌어도 slug 패턴으로 Sol/Astra 역할 고정
- `previous-generation`, `deprecated` 모델 자동 제외
- AI 분류기는 기본 비활성; 필요 시 낮은 confidence에서만 second opinion 가능

## 추천 알고리즘

1. 명시적 모델·reasoning 지시를 최우선 적용합니다.
2. 범위, 변경 규모, 위험도, 분석/구현 요구를 규칙으로 점수화합니다.
3. reasoning 점수와 model 점수를 분리합니다. 단순 작업은 Luna/LOW, 일반 작업은 Terra/MEDIUM, 전문 구현은 Sol/HIGH, 프로젝트 전체·고위험 작업은 Astra/XHIGH까지 올라갑니다.
4. confidence는 고정값이 아닙니다. 매칭 규칙 수(최대 0.42), 경계와의 거리(최대 0.16), 위험도(+0.10), 상충 증거(-0.15)를 합산하며 매칭이 없으면 `0.15`입니다.
5. confidence가 `0.50` 미만이고 위험도 floor가 없으면 `FAST/LOW`로 낮춥니다.
6. 모델 역할 키워드와 slug 패턴을 함께 평가해 카탈로그 설명 변경에도 안정적으로 선택합니다.

## 예시

| 프롬프트 유형 | 추천 |
|---|---|
| 모호한 요청 | GPT-5.6-Luna · LOW · confidence 0.15 |
| 여러 파일 일괄 이름 변경 | GPT-5.6-Sol · LOW |
| 알고리즘 복잡도 분석 | GPT-5.6-Terra · HIGH |
| 복잡한 버그 분석·수정 | GPT-5.6-Sol · HIGH |
| 전체 아키텍처·보안·마이그레이션 | GPT-6-Astra · XHIGH |

## 실행 및 검증

```powershell
py -m pip install .
codex --yolo
```

추천 화면에서 `Y`는 추천 적용 후 실행, `N`은 현재 설정 유지입니다. 비활성화:

```powershell
$env:ADAPTIVE_MODEL_ROUTER_ENABLED = "0"
```

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
$env:ADAPTIVE_MODEL_ROUTER_CONFIG = "$PWD\router_config.json"
py -m unittest discover -s tests -v
```

현재 검증: **37개 테스트 통과**, `compileall`, JSON 파싱, `git diff --check` 통과. Native bridge 매트릭스에서도 네 모델과 LOW/HIGH/XHIGH가 난이도별로 분기됩니다.

## 설정

- 저장소 기본값: [`router_config.json`](router_config.json)
- 설치 후 설정: `%LOCALAPPDATA%\AdaptiveModelRouter\router_config.json`
- `rules`에서 키워드·점수·위험도·충돌 여부 조정
- `model_selection.slug_role_patterns`에 신규 모델 역할 패턴 추가

## 제한사항

Codex provider 범위에서만 선택하며 provider 자체는 자동 전환하지 않습니다. 실제 호출은 Y/N 확인 뒤 Codex가 수행합니다. AI second opinion은 비용·지연·순환 라우팅 위험 때문에 기본 비활성입니다.

## License

MIT
