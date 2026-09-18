"""Invariants that hold for every rule and every prompt, not for a listed example.

The prompt matrix in `test_prompt_matrix.py` and `test_claude_matrix.py` checks cases
someone thought of.  Every routing defect this project has actually shipped was a case
nobody thought of, and each had a shape that a property could have caught:

  - A rule excluded a word so another rule would take it, and no other rule accepted it.
    "에러 ... 분석해주고 고쳐주라" was excluded by bug_diagnosis (none_of "고쳐") and fell
    through to the 0.15-confidence fallback, which recommended the cheapest tier.
  - A risk vocabulary named one phrasing of an operation and missed its synonyms, so
    "실서버에 적용" routed like a typo fix while "운영 서버에 배포" routed as high impact.
  - A term short enough to be a prefix of an unrelated word fired on it: "deploy" matched
    "deployment" and turned a README wording change into a production operation.
  - The hook blocked a prompt it could say nothing about, in a session whose model it
    could not read, which is a state a blocked session can never leave.

Each class gets a property here rather than another example.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.config import PACKAGED_CONFIG, load_config
from adaptive_model_router.hook import evaluate_hook
from adaptive_model_router.scorer import _matches_rule, score_prompt


# Phrasings of "diagnose this and also fix it".  bug_diagnosis deliberately excludes every
# fixing verb so it stays a diagnosis rule; analysis_and_implementation is the rule that is
# supposed to catch them.  Nothing enforced that pairing until this test.
FIX_VERBS = ("고쳐주라", "고쳐줘", "고치고", "수정해줘", "fix it")

# Operations that change or destroy something outside the working tree.  Each must raise the
# risk floor no matter which wording the user reaches for.
HIGH_IMPACT_PHRASINGS = (
    "이제 실서버에 적용해주라",
    "실서버 배포해줘",
    "실 서버에 반영해줘",
    "라이브 서버에 올려줘",
    "실제 서버에 반영해줘",
    "운영 서버에 배포해줘",
    "프로덕션 배포해줘",
    "production config 바꿔줘",
    "DB migration 실행해줘",
    "데이터베이스 초기화해줘",
    "db 초기화 해줘",
    "테이블 드롭해줘",
    "테이블 삭제해줘",
    "drop table 실행해줘",
    "백업 삭제해줘",
    "전부 지워줘",
    "모두 지워줘",
    "권한 변경해줘",
    "보안 취약점을 점검해줘",
)

# Wordings that contain a high-impact term as a substring but describe a documentation or
# text edit.  These are the false positives a greedy token produces.
BENIGN_LOOKALIKES = (
    "README에서 deployment 용어만 바꿔줘",
    "README에 security라는 단어 한 줄 추가해줘",
    "문서에 security 문자열만 추가해줘",
    "README에서 production이라는 문자열만 바꿔줘",
)


class RuleHandoffTests(unittest.TestCase):
    """An exclusion in one rule is a delegation to another; nothing checked the recipient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def _rule(self, rule_id: str) -> dict:
        return next(rule for rule in self.config["rules"] if rule["id"] == rule_id)

    def test_every_fix_verb_bug_diagnosis_excludes_is_caught_by_another_rule(self) -> None:
        for verb in FIX_VERBS:
            prompt = f"로그인 에러의 원인을 분석하고 {verb}"
            with self.subTest(verb=verb):
                scored = score_prompt(prompt, self.config)
                self.assertFalse(
                    scored.uncertain_default_used,
                    f"{prompt!r} matched no rule: bug_diagnosis excludes the fixing verb and "
                    "nothing else accepts it, so it lands on the low-confidence fallback.",
                )
                self.assertIn("analysis_and_implementation", [m.rule_id for m in scored.matches])

    def test_the_excluding_rule_still_excludes(self) -> None:
        """The hand-off must not be achieved by simply deleting the exclusion."""
        scored = score_prompt("로그인 에러의 원인을 분석하고 고쳐줘", self.config)
        self.assertNotIn("bug_diagnosis", [m.rule_id for m in scored.matches])

    def test_diagnosis_without_a_fix_still_reaches_the_diagnosis_rule(self) -> None:
        scored = score_prompt("로그인 오류의 원인을 조사해줘", self.config)
        self.assertIn("bug_diagnosis", [m.rule_id for m in scored.matches])


class HighImpactVocabularyTests(unittest.TestCase):
    """A risk rule is only as good as the phrasings it knows."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_every_high_impact_phrasing_raises_the_risk_floor(self) -> None:
        for prompt in HIGH_IMPACT_PHRASINGS:
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                self.assertIn(
                    "high_impact_operation", [m.rule_id for m in scored.matches],
                    f"{prompt!r} describes an operation outside the working tree but does not "
                    "raise the risk floor.",
                )
                self.assertIn(scored.reasoning, ("HIGH", "XHIGH", "MAX", "ULTRA"))

    def test_a_text_edit_that_merely_mentions_one_is_not_high_impact(self) -> None:
        """"deploy" once matched "deployment" and turned a README edit into a deploy."""
        for prompt in BENIGN_LOOKALIKES:
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                self.assertNotIn("high_impact_operation", [m.rule_id for m in scored.matches])
                self.assertEqual(("FAST", "LOW"), (scored.profile, scored.reasoning))

    def test_no_risk_term_is_a_prefix_of_a_longer_benign_word_in_the_corpus(self) -> None:
        """A short token is a greedy token; check the ones we ship against known wordings."""
        rule = next(r for r in self.config["rules"] if r["id"] == "high_impact_operation")
        for prompt in BENIGN_LOOKALIKES:
            text = " ".join(prompt.casefold().split())
            for term in rule["any_of"]:
                if term.casefold() in text:
                    stripped = text
                    for phrase in rule.get("literal_mentions", []):
                        stripped = stripped.replace(phrase.casefold(), " ")
                    self.assertNotIn(
                        term.casefold(), stripped,
                        f"risk term {term!r} fires on {prompt!r}, which is a text edit",
                    )


class HookNeverTrapsASessionTests(unittest.TestCase):
    """The block is answered by resubmitting identical text.  A session that cannot produce
    that text must never be held, or it is a latch rather than a gate."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def _blocking_config(self) -> dict:
        return dict(self.config, hook=dict(self.config["hook"], claude_modes={"cli": "block"}))

    def _run(self, prompt: str, directory: str, transcript: str = "", model: str | None = None) -> dict:
        with patch.dict("os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}), patch(
            "adaptive_model_router.hook.load_current_claude_config", return_value=(model, None),
        ):
            return evaluate_hook(
                {"session_id": "trap", "prompt": prompt, "transcript_path": transcript},
                self._blocking_config(), provider="claude",
            )

    def test_a_session_with_no_completed_turn_is_never_blocked(self) -> None:
        """A block prevents the turn that records the model, and the model is what would
        let the router decide the block was unnecessary.  Observed live as a terminal
        session with zero turns and three consecutive blocks."""
        prompts = (
            "지금 이게 무슨 상황이야?",
            "자꾸 아래처럼 뜨는데 원인이 뭐야?",
            "전체 프로젝트 보안 취약점을 점검하고 수정해줘",
            "운영 서버의 DB 백업을 삭제해줘",
        )
        with tempfile.TemporaryDirectory() as directory:
            for prompt in prompts:
                with self.subTest(prompt=prompt):
                    response = self._run(prompt, directory)
                    self.assertNotIn(
                        "decision", response,
                        "a session that has completed no turn cannot learn its model, so a "
                        "block here can never be lifted by the router itself",
                    )

    def test_three_different_prompts_in_a_row_never_all_block(self) -> None:
        """Rephrasing is what users do when an unexpected screen appears, and every
        rephrasing is a new first submission under the approval's exact-text key."""
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "s.jsonl"
            transcript.write_text(
                '{"type": "assistant", "effort": "low", '
                '"message": {"role": "assistant", "model": "claude-haiku-4-5"}}\n',
                encoding="utf-8",
            )
            blocked = [
                "decision" in self._run(prompt, directory, str(transcript))
                for prompt in ("이거 왜 이래?", "그럼 어떻게 해야 해?", "다시 설명해줘")
            ]
        self.assertNotEqual([True, True, True], blocked)

    def test_a_block_always_carries_a_reason_and_a_known_current_setting(self) -> None:
        """Whenever the hook does block, all three preconditions must hold: a matched rule,
        a known current model, and a recommendation that differs from it."""
        cases = (
            ("전체 프로젝트 보안 취약점을 점검하고 수정해줘", "claude-haiku-4-5", "low"),
            ("운영 서버의 DB 백업을 삭제해줘", "claude-haiku-4-5", "low"),
            ("지금 이게 무슨 상황이야?", "claude-haiku-4-5", "low"),
            ("README 오타 하나만 고쳐줘", "claude-haiku-4-5", "low"),
        )
        for prompt, model, effort in cases:
            with self.subTest(prompt=prompt), tempfile.TemporaryDirectory() as directory:
                transcript = Path(directory) / "s.jsonl"
                transcript.write_text(
                    f'{{"type": "assistant", "effort": "{effort}", '
                    f'"message": {{"role": "assistant", "model": "{model}"}}}}\n',
                    encoding="utf-8",
                )
                response = self._run(prompt, directory, str(transcript))
                if "decision" not in response:
                    continue
                scored = score_prompt(prompt, self.config)
                self.assertFalse(
                    scored.uncertain_default_used,
                    f"blocked {prompt!r} with no matched rule; the screen cannot say why",
                )
                self.assertIn("Current Model:", response["reason"])
                self.assertNotIn("Current Model: UNKNOWN", response["reason"])

    def test_the_bypass_works_even_on_the_highest_risk_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "s.jsonl"
            transcript.write_text(
                '{"type": "assistant", "effort": "low", '
                '"message": {"role": "assistant", "model": "claude-haiku-4-5"}}\n',
                encoding="utf-8",
            )
            response = self._run("라우터 꺼 운영 서버의 DB 백업을 삭제해줘", directory, str(transcript))
        self.assertEqual({"continue": True}, response)


class EveryRuleIsReachableTests(unittest.TestCase):
    """A rule nothing can match is configuration that looks like behaviour."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_each_rule_matches_a_prompt_built_from_its_own_vocabulary(self) -> None:
        for rule in self.config["rules"]:
            with self.subTest(rule=rule["id"]):
                if rule.get("all_of"):
                    probe = " ".join(group[0] for group in rule["all_of"])
                else:
                    probe = rule["any_of"][0]
                self.assertTrue(
                    _matches_rule(" ".join(probe.casefold().split()), dict(rule)),
                    f"{rule['id']} does not match a prompt assembled from its own terms",
                )

    def test_every_rule_declares_the_scores_the_scorer_reads(self) -> None:
        for rule in self.config["rules"]:
            with self.subTest(rule=rule["id"]):
                self.assertIn("score", rule)
                self.assertIsInstance(rule["score"], int)
                self.assertIsInstance(rule.get("model_score", rule["score"]), int)


# A user reporting that something does not work rarely uses the word "error".  Every rule
# that recognised a fault required one of 오류 / 에러 / error / 버그 / 실패 to be present, so
# the most ordinary bug report there is - "이게 안 되는데" - matched nothing and was routed
# to the cheapest tier with the reason "no matching routing evidence".  Reported live from a
# Codex session: a spreadsheet automation question got GPT-5.6-Luna / LOW.
MALFUNCTION_PHRASINGS = (
    "이 시트 자동화에서 종목별 증감분석 현황이 적히지 않는데 이유가 뭐야?",
    "이 값이 안 적히는데 왜 그래?",
    "버튼을 눌러도 아무 반응이 없어",
    "결과가 안 나오는데 확인해줘",
    "화면에 표시가 안 돼",
    "스케줄이 동작하지 않아",
    "반영이 안 되는데 원인 좀 찾아줘",
    "데이터가 누락돼서 들어와",
    "값이 빠져있어",
    "저장이 안 됨",
    "왜 아직 Stable로 남아있는거야?",
    "자동 수집이 실행되지 않았어",
    "목록이 비어있어",
    "화면이 먹통이야",
)

# The same reports stripped of every other signal - no automation, no spreadsheet, no error
# word - so the malfunction rule is the only thing that can classify them.  Without these
# the corpus above passes even with the rule deleted, because its members carry a second
# signal that classifies them for an unrelated reason.
SYMPTOM_ONLY_PHRASINGS = (
    "이 값이 기록되지 않아",
    "메일이 발송되지 않는데 확인해줘",
    "목록이 갱신되지 않습니다",
    "알림이 오지 않음",
    "버튼이 눌리지 않는데 왜 그럴까",
)

# The same words in a causative construction: the user is asking for something to be made
# not to happen.  That is a feature request, not a fault report, and must not raise the tier
# through the malfunction rule.
CAUSATIVE_NOT_MALFUNCTION = (
    "데스크톱앱에서는 작동 안되게 해주고 cli로만 실행되게 해줘",
    "알림이 뜨지 않도록 해줘",
    "자동 저장이 안 되게 해줘",
)


class MalfunctionReportTests(unittest.TestCase):
    """"It isn't working" is the most common bug report and the least likely to say "error"."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_every_symptom_phrasing_is_recognised_as_work(self) -> None:
        for prompt in MALFUNCTION_PHRASINGS + SYMPTOM_ONLY_PHRASINGS:
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                self.assertFalse(
                    scored.uncertain_default_used,
                    f"{prompt!r} reports something not working and matched no rule, so it "
                    "lands on the cheapest tier with no stated reason",
                )

    def test_every_symptom_phrasing_reaches_the_malfunction_rule_itself(self) -> None:
        """Not merely "some rule matched": several of these carry a second signal, so a
        corpus checked only for classification passes with the malfunction rule deleted."""
        for prompt in MALFUNCTION_PHRASINGS + SYMPTOM_ONLY_PHRASINGS:
            with self.subTest(prompt=prompt):
                matched = [m.rule_id for m in score_prompt(prompt, self.config).matches]
                self.assertIn(
                    "malfunction_report", matched,
                    f"{prompt!r} is a fault report but is classified by {matched} instead, so "
                    "the same sentence about anything else would fall through",
                )

    def test_no_symptom_phrasing_is_routed_to_the_cheapest_tier(self) -> None:
        """The defect the user reported was the tier, not the confidence."""
        for prompt in MALFUNCTION_PHRASINGS + SYMPTOM_ONLY_PHRASINGS:
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                self.assertNotEqual(
                    ("FAST", "LOW"), (scored.profile, scored.reasoning),
                    f"{prompt!r} is a diagnosis request routed to the smallest model at the "
                    "lowest effort",
                )

    def test_a_causative_negation_is_a_feature_request_not_a_fault(self) -> None:
        for prompt in CAUSATIVE_NOT_MALFUNCTION:
            with self.subTest(prompt=prompt):
                matched = [m.rule_id for m in score_prompt(prompt, self.config).matches]
                self.assertNotIn("malfunction_report", matched)

    def test_a_fault_stated_with_and_without_the_word_error_routes_the_same(self) -> None:
        """The tier must follow the task, not the vocabulary the user happened to reach for."""
        pairs = (
            ("로그인 오류의 원인을 조사해줘", "로그인이 안 되는데 원인 좀 조사해줘"),
            ("저장 에러가 나는데 확인해줘", "저장이 안 되는데 확인해줘"),
        )
        for with_word, without_word in pairs:
            with self.subTest(pair=(with_word, without_word)):
                a = score_prompt(with_word, self.config)
                b = score_prompt(without_word, self.config)
                self.assertEqual((a.profile, a.reasoning), (b.profile, b.reasoning))


class DiagnosisRulesDoNotStackTests(unittest.TestCase):
    """bug_diagnosis and malfunction_report describe one thing two ways."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_both_diagnosis_rules_share_a_group(self) -> None:
        groups = {
            rule["id"]: rule.get("group")
            for rule in self.config["rules"]
            if rule["id"] in ("bug_diagnosis", "malfunction_report")
        }
        self.assertEqual({"bug_diagnosis": "diagnosis", "malfunction_report": "diagnosis"}, groups)

    def test_matching_both_counts_once(self) -> None:
        prompt = "저장 오류가 나는데 저장이 되지 않는 원인을 조사해줘"
        scored = score_prompt(prompt, self.config)
        matched = {m.rule_id for m in scored.matches}
        self.assertLessEqual({"bug_diagnosis", "malfunction_report"} & matched, matched)
        single = score_prompt("저장 오류의 원인을 조사해줘", self.config)
        self.assertEqual(single.profile, scored.profile)


if __name__ == "__main__":
    unittest.main()
