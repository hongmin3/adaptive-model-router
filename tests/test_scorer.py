from __future__ import annotations

import unittest

from adaptive_model_router.config import load_config
from adaptive_model_router.scorer import score_prompt


class PromptScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()

    def assert_level(self, prompt: str, expected: str) -> None:
        result = score_prompt(prompt, self.config)
        self.assertEqual(expected, result.level, f"score={result.score}, matches={result.matches}")

    def test_required_prompt_classes(self) -> None:
        cases = {
            "README 오타 수정해줘": "LOW",
            "Python으로 파일 정리 자동화 기능을 구현해줘": "MEDIUM",
            "로그인 실패 버그를 분석하고 수정해줘": "MEDIUM",
            "원인 불명의 복잡한 동시성 버그의 근본 원인을 분석하고 수정해줘": "HIGH",
            "이 프로젝트 전체를 분석해서 아키텍처와 모듈 구조를 개선하고 테스트해줘": "HIGH",
            "README에 architecture라는 단어 한 줄 추가해줘": "LOW",
            "DB migration 실행해줘": "HIGH",
            (
                "README의 설치 섹션에 Windows PowerShell, CMD, Windows Terminal에서 동일하게 사용할 수 있다는 설명을 "
                "여러 문장으로 자세히 추가하고 예시 명령을 문서 형식에 맞게 정리해줘"
            ): "LOW",
            "이 기능 좀 봐줘": "MEDIUM",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                self.assert_level(prompt, expected)

    def test_length_alone_does_not_raise_complexity(self) -> None:
        prompt = "README 문구를 자연스럽게 수정해줘. " + "설명 문장을 유지해줘. " * 100
        self.assert_level(prompt, "LOW")

    def test_explicit_reasoning_wins(self) -> None:
        result = score_prompt("README 오타 수정이지만 reasoning high로 해", self.config)
        self.assertEqual("HIGH", result.reasoning)
        self.assertTrue(result.explicit_reasoning)


if __name__ == "__main__":
    unittest.main()
