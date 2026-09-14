# Codex Fork Maintenance

## 자동 경로

1. GitHub Actions가 매일 `openai/codex`의 최신 정식 Release tag를 확인한다.
2. 대응하는 `adaptive-<official-tag>` Release가 없으면 공식 소스를 clone한다.
3. `codex-patches/adaptive-router.patch`를 check 후 적용한다.
4. Python Router test와 Windows Rust release build를 실행한다.
5. 성공한 경우에만 zip과 SHA-256을 public Release로 발행한다.
6. PC의 `AdaptiveModelRouterUpdate` 작업이 새 Release를 검증하고 설치한다.

## 충돌 발생 시

Workflow는 open Issue를 자동 생성한다. 다음 순서로 패치를 갱신한다.

```powershell
git clone --branch <new-official-tag> https://github.com/openai/codex.git upstream-codex
git -C upstream-codex apply --3way .\codex-patches\adaptive-router.patch
```

충돌 범위는 원칙적으로 다음 세 파일에 제한한다.

- `codex-rs/tui/src/chatwidget.rs`
- `codex-rs/tui/src/chatwidget/constructor.rs`
- `codex-rs/tui/src/chatwidget/input_submission.rs`

공식 코드의 새 Prompt 제출 경로와 모델 설정 API를 확인하고 최소 변경으로 다시 적용한다. 기존 patch의 check를 우회하지 않는다.

## 검증 기준

- `cargo fmt --all -- --check`
- `cargo check -p codex-tui`
- 관련 Codex TUI test
- `py -m unittest discover -s tests -v`
- Y/N에서 원래 Prompt가 정확히 한 번 제출되는지 확인
- Router/Python 오류에서 Codex가 fail-open 하는지 확인
- zip checksum과 설치 후 `codex --version` 확인

검증 후 patch를 갱신하고 Workflow를 `upstream_tag` 입력과 함께 재실행한 다음 자동 생성 Issue를 닫는다.

## Rollback

설치 전 바이너리는 `%LOCALAPPDATA%\AdaptiveModelRouter\backups\<timestamp>`에 보관된다. 긴급 복구 시 사용자 PATH에서 `%LOCALAPPDATA%\AdaptiveModelRouter\bin`을 제거하면 기존 npm Codex가 다시 우선된다.
