from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "XHIGH": 3}


@dataclass(frozen=True)
class Match:
    rule_id: str
    label: str
    score: int
    confidence_conflict: bool = True


@dataclass(frozen=True)
class ScoreResult:
    score: int
    model_score: int
    level: str
    profile: str
    reasoning: str
    confidence: float
    confidence_reason: str
    matches: tuple[Match, ...]
    reason: str
    uncertain_default_used: bool
    keep_current_model: bool
    explicit_reasoning: bool


def _contains(text: str, terms: list[str]) -> bool:
    return any(term.casefold() in text for term in terms)


def _matches_rule(text: str, rule: dict[str, Any]) -> bool:
    if rule.get("none_of") and _contains(text, rule["none_of"]):
        return False
    if rule.get("any_of") and not _contains(text, rule["any_of"]):
        return False
    for group in rule.get("all_of", []):
        if not _contains(text, group):
            return False
    return bool(rule.get("any_of") or rule.get("all_of"))


def _score_level(score: int, thresholds: dict[str, int]) -> str:
    if score <= thresholds["low_max"]:
        return "LOW"
    if score <= thresholds["medium_max"]:
        return "MEDIUM"
    if score <= thresholds["high_max"]:
        return "HIGH"
    return "XHIGH"


def _confidence(
    score: int, matches: list[Match], thresholds: dict[str, int], risk_floor: str | None,
) -> tuple[float, str]:
    if not matches:
        return 0.15, "no matching routing evidence"

    boundaries = [thresholds["low_max"], thresholds["medium_max"], thresholds["high_max"]]
    distance = min(abs(score - boundary) for boundary in boundaries)
    evidence = min(0.42, 0.14 * len(matches))
    margin = min(0.16, 0.04 * distance)
    risk = 0.10 if risk_floor else 0.0
    conflict_matches = [match for match in matches if match.confidence_conflict]
    conflict = 0.15 if (
        any(match.score < 0 for match in conflict_matches)
        and any(match.score > 0 for match in conflict_matches)
    ) else 0.0
    confidence = round(max(0.0, min(0.99, 0.32 + evidence + margin + risk - conflict)), 2)
    reason = (
        f"evidence={evidence:.2f} ({len(matches)} rule match{'es' if len(matches) != 1 else ''}); "
        f"margin={margin:.2f}; risk={risk:.2f}; conflict=-{conflict:.2f}"
    )
    return confidence, reason


def score_prompt(prompt: str, config: dict[str, Any]) -> ScoreResult:
    text = " ".join(prompt.casefold().split())
    score = int(config["defaults"]["score"])
    model_score = int(config["defaults"].get("model_score", score))
    matches: list[Match] = []
    risk_floor: str | None = None

    for rule in config["rules"]:
        if not _matches_rule(text, rule):
            continue
        delta = int(rule["score"])
        score += delta
        model_score += int(rule.get("model_score", delta))
        matches.append(Match(
            rule["id"], rule["label"], delta,
            bool(rule.get("confidence_conflict", True)),
        ))
        floor = rule.get("risk_floor")
        if floor and (risk_floor is None or LEVEL_ORDER[floor] > LEVEL_ORDER[risk_floor]):
            risk_floor = floor

    score = max(0, score)
    model_score = max(0, model_score)
    level = _score_level(score, config["thresholds"])
    model_level = _score_level(model_score, config["thresholds"])
    if risk_floor and LEVEL_ORDER[risk_floor] > LEVEL_ORDER[level]:
        level = risk_floor

    confidence, confidence_reason = _confidence(score, matches, config["thresholds"], risk_floor)
    uncertain = confidence < float(config["defaults"]["confidence_threshold"]) and risk_floor is None
    if uncertain:
        level = config["defaults"]["uncertain_reasoning"]

    explicit_reasoning = False
    for requested_level, phrases in config.get("directives", {}).get("reasoning", {}).items():
        if _contains(text, phrases):
            level = requested_level
            explicit_reasoning = True

    profile = {"LOW": "FAST", "MEDIUM": "BALANCED", "HIGH": "STRONG", "XHIGH": "MAX"}[model_level]
    if uncertain:
        profile = config["defaults"]["uncertain_profile"]
    reasoning = level
    keep_current = _contains(text, config.get("directives", {}).get("keep_current_model", []))
    reason = "explicit user setting" if explicit_reasoning or keep_current else (
        ", ".join(match.label for match in matches[:3]) or "ambiguous request; balanced default"
    )
    return ScoreResult(
        score, model_score, level, profile, reasoning, confidence, confidence_reason, tuple(matches), reason, uncertain,
        keep_current, explicit_reasoning,
    )
