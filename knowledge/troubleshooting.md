## 알려진 이슈
<!-- akela: id=known-issues scope=all tier=should -->

CLI가 계정별 사용량 상태를 기계 판독 가능한 형태로 제공하지 않으면 Router가 이를 확정할 수 없다. 이 경우 `--status-file`로 검증된 로컬 상태를 전달하며, reset time이 없으면 `Unknown`으로 표시한다.

## 점검 순서
<!-- akela: id=check-order scope=all tier=should -->

1. `codex --version` 또는 `claude --version`으로 CLI 존재 여부를 확인한다.
2. `--no-exec --debug`로 점수와 매칭 규칙을 확인한다.
3. `router_config.json`의 임계값과 문맥 조건을 확인한다.
4. 모델 목록이 UNKNOWN이면 해당 CLI의 로컬 catalog/help 출력이 현재 버전에서 제공되는지 확인한다.
5. `py -m unittest discover -s tests -v`로 회귀 테스트를 실행한다.
