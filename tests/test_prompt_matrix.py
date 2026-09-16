from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from adaptive_model_router.cli import _print_debug
from adaptive_model_router.config import PACKAGED_CONFIG, load_config
from adaptive_model_router.scorer import score_prompt


class DiversePromptRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_prompt_matrix(self) -> None:
        # These expectations describe task cost and difficulty, independent of
        # the current rule weights or whichever models happen to be installed.
        cases = (
            ("README 오타 수정해줘", "FAST", "LOW"),
            ("README에 architecture라는 단어 한 줄 추가해줘", "FAST", "LOW"),
            ("이 문장을 자연스럽게 번역해줘", "FAST", "LOW"),
            ("변수명 한 줄만 바꿔줘", "FAST", "LOW"),
            ("문자열 검색 실행해줘", "FAST", "LOW"),
            ("이 요청을 처리해줘", "FAST", "LOW"),
            ("Python 자동화 기능을 구현해줘", "BALANCED", "MEDIUM"),
            ("API 연동 기능 추가해줘", "BALANCED", "MEDIUM"),
            ("README 오타를 수정하고 API 연동 기능도 구현해줘", "BALANCED", "MEDIUM"),
            ("설정 변경해줘", "BALANCED", "MEDIUM"),
            ("로그인 실패 버그를 분석하고 수정해줘", "BALANCED", "MEDIUM"),
            ("코드 리뷰해줘", "BALANCED", "MEDIUM"),
            ("이 코드의 버그를 찾아줘", "BALANCED", "MEDIUM"),
            ("알고리즘 복잡도 분석해줘", "BALANCED", "HIGH"),
            ("수학적 증명을 검토해줘", "BALANCED", "HIGH"),
            ("Fix critical concurrency bug in payments", "STRONG", "HIGH"),
            ("원인 불명의 복잡한 버그를 분석하고 수정해줘", "STRONG", "HIGH"),
            ("Refactor architecture", "STRONG", "HIGH"),
            ("여러 파일의 변수 이름을 일괄 변경해줘", "STRONG", "LOW"),
            ("프로젝트 전체를 분석해줘", "STRONG", "HIGH"),
            ("모든 파일을 검토해줘", "STRONG", "HIGH"),
            ("전수조사해서 개선할 부분을 확인해줘", "STRONG", "HIGH"),
            (
                "프로젝트 전체 아키텍처의 근본 원인을 분석하고 보안 코드를 수정한 뒤 테스트해줘",
                "MAX", "XHIGH",
            ),
            ("운영 서버의 DB 백업을 삭제해줘", "STRONG", "HIGH"),
            ("production deploy 해줘", "STRONG", "HIGH"),
            ("DB migration 실행해줘", "STRONG", "HIGH"),
            ("README에 security라는 단어 한 줄 추가해줘", "FAST", "LOW"),
            (
                "README에 security라는 단어를 추가하고 운영 서버 설정도 변경해줘",
                "STRONG", "HIGH",
            ),
            ("사용량 아껴서 프로젝트 전체를 분석해줘", "STRONG", "LOW"),
            ("테스트 코드 작성해줘", "BALANCED", "MEDIUM"),
            ("테스트만 실행해줘", "FAST", "MEDIUM"),
            ("권한 변경해줘", "STRONG", "HIGH"),
            ("보안 코드 수정해줘", "STRONG", "HIGH"),
            ("Fix race condition", "STRONG", "HIGH"),
            ("여러 모듈의 의존성 분석해줘", "BALANCED", "MEDIUM"),
            ("reasoning high로 README 오타 수정해줘", "FAST", "HIGH"),
            ("README 설치 절차 문구만 수정해줘", "FAST", "LOW"),
            ("JSON 키 이름을 한 줄 변경해줘", "FAST", "LOW"),
            ("Explain what this line does", "FAST", "LOW"),
            ("새 함수 기능 구현하고 테스트해줘", "BALANCED", "MEDIUM"),
            ("새 API endpoint를 구현해줘", "BALANCED", "MEDIUM"),
            ("단일 함수의 버그 수정해줘", "BALANCED", "MEDIUM"),
            ("로그인 오류의 원인을 분석해줘", "BALANCED", "MEDIUM"),
            ("Error 로그를 조사해줘", "BALANCED", "MEDIUM"),
            ("컴포넌트 의존성 분석해줘", "BALANCED", "MEDIUM"),
            ("SQL 쿼리 최적화해줘", "BALANCED", "HIGH"),
            ("성능 병목 원인을 분석해줘", "STRONG", "HIGH"),
            ("보안 취약점을 점검해줘", "STRONG", "HIGH"),
            ("프로덕션 배포해줘", "STRONG", "HIGH"),
            ("프로젝트 전체에 대해 코드 리뷰해줘", "STRONG", "HIGH"),
            ("Review all files for improvements", "STRONG", "HIGH"),
            ("프로젝트 전체에서 동일 문자열 일괄 교체해줘", "STRONG", "LOW"),
            ("모든 파일의 변수명을 일괄 변경해줘", "STRONG", "LOW"),
            ("여러 파일을 리네임해줘", "STRONG", "LOW"),
            ("문서에 security 문자열만 추가해줘", "FAST", "LOW"),
            ("README에서 production이라는 문자열만 바꿔줘", "FAST", "LOW"),
            ("README 오타 수정 후 코드 리뷰해줘", "BALANCED", "MEDIUM"),
            ("README의 한 문장 오탈자만 고쳐줘", "FAST", "LOW"),
            ("REST API 엔드포인트를 새로 만들어줘", "BALANCED", "MEDIUM"),
            ("OAuth 로그인 버그의 원인을 조사해줘", "BALANCED", "MEDIUM"),
            ("서로 다른 세 모듈의 의존 관계를 파악해줘", "BALANCED", "MEDIUM"),
            ("전체 저장소를 리뷰해줘", "STRONG", "HIGH"),
            ("replace all occurrences of a name across files", "STRONG", "LOW"),
            ("Explain this algorithm", "FAST", "LOW"),
            ("운영 DB에서 테이블을 삭제해줘", "STRONG", "HIGH"),
            ("README에서 deployment 용어만 바꿔줘", "FAST", "LOW"),
            ("프로덕션 인증 코드를 고쳐줘", "STRONG", "HIGH"),
            ("동시성 경합 문제를 해결해줘", "STRONG", "HIGH"),
            ("이름만 바꿔줘", "FAST", "LOW"),
            ("일주일치 로그를 분석해서 오류 패턴 찾아줘", "BALANCED", "MEDIUM"),
            ("전체 프로젝트 보안 취약점을 점검하고 수정해줘", "MAX", "XHIGH"),
        )
        for prompt, profile, reasoning in cases:
            with self.subTest(prompt=prompt):
                actual = score_prompt(prompt, self.config)
                self.assertEqual((profile, reasoning), (actual.profile, actual.reasoning))

    def test_explicit_scope_evidence_survives_low_confidence(self) -> None:
        result = score_prompt("프로젝트 전체를 분석해줘", self.config)
        self.assertTrue(result.matches)
        self.assertFalse(result.uncertain_default_used)
        self.assertGreaterEqual(result.confidence, 0.50)

    def test_unknown_request_has_no_evidence(self) -> None:
        result = score_prompt("opaque task", self.config)
        self.assertEqual(("FAST", "LOW"), (result.profile, result.reasoning))
        self.assertEqual(0.15, result.confidence)
        self.assertTrue(result.uncertain_default_used)

    def test_debug_fallback_names_the_actual_recommendation(self) -> None:
        result = score_prompt("opaque task", self.config)
        output = StringIO()
        with redirect_stdout(output):
            _print_debug(result)
        self.assertIn("Fallback:\nFAST / LOW", output.getvalue())
        self.assertIn("Model Score: 2", output.getvalue())
        self.assertIn("no matching routing evidence", output.getvalue())

    def test_root_and_packaged_config_route_the_same_prompts(self) -> None:
        root_config = load_config(Path(__file__).resolve().parents[1] / "router_config.json")
        for prompt in ("프로젝트 전체를 분석해줘", "README 오타 수정해줘", "운영 서버의 DB 백업을 삭제해줘"):
            with self.subTest(prompt=prompt):
                a = score_prompt(prompt, self.config)
                b = score_prompt(prompt, root_config)
                self.assertEqual((a.profile, a.reasoning), (b.profile, b.reasoning))


if __name__ == "__main__":
    unittest.main()
