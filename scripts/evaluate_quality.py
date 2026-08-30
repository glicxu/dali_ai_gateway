from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence, TypeVar


T = TypeVar("T")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate blinded AI quality results without printing content."
    )
    parser.add_argument("results", type=Path, help="Private evaluation JSONL file")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.results), indent=2, sort_keys=True))


def evaluate(path: Path) -> dict[str, object]:
    totals: dict[str, dict[str, object]] = defaultdict(_empty_totals)
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, raw in enumerate(source, start=1):
            if not raw.strip():
                continue
            try:
                sample = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number}") from error
            _consume_sample(sample, totals, seen, line_number)
    return {
        "sample_count": len(seen),
        "candidates": {
            candidate: _finalize(values) for candidate, values in sorted(totals.items())
        },
    }


def _consume_sample(
    sample: object,
    totals: dict[str, dict[str, object]],
    seen: set[str],
    line_number: int,
) -> None:
    if not isinstance(sample, dict):
        raise ValueError(f"line {line_number} must be an object")
    sample_id = sample.get("sample_id")
    reference = sample.get("reference")
    candidates = sample.get("candidates")
    if not isinstance(sample_id, str) or not sample_id or sample_id in seen:
        raise ValueError(f"line {line_number} has an invalid or duplicate sample ID")
    if not isinstance(reference, dict) or not isinstance(candidates, dict):
        raise ValueError(f"line {line_number} has invalid evaluation data")
    seen.add(sample_id)
    reference_transcript = reference.get("transcript")
    reference_translations = reference.get("translations", {})
    if not isinstance(reference_transcript, str) or not isinstance(
        reference_translations, dict
    ):
        raise ValueError(f"line {line_number} has invalid references")
    for candidate_name, candidate in candidates.items():
        if not isinstance(candidate_name, str) or not isinstance(candidate, dict):
            raise ValueError(f"line {line_number} has invalid candidate data")
        values = totals[candidate_name]
        values["samples"] = int(values["samples"]) + 1
        transcript = candidate.get("transcript")
        if isinstance(transcript, str):
            _add_transcription(values, reference_transcript, transcript)
        translations = candidate.get("translations", {})
        if isinstance(translations, dict):
            for language, expected in reference_translations.items():
                actual = translations.get(language)
                if isinstance(expected, str) and isinstance(actual, str):
                    cast_scores = values["translation_chrf"]
                    assert isinstance(cast_scores, list)
                    cast_scores.append(_chrf(expected, actual))
        _add_latency(values, candidate.get("latency_ms"))
        _add_human_scores(values, candidate.get("human"))


def _empty_totals() -> dict[str, object]:
    return {
        "samples": 0,
        "word_edits": 0,
        "reference_words": 0,
        "character_edits": 0,
        "reference_characters": 0,
        "translation_chrf": [],
        "first_partial_ms": [],
        "final_ms": [],
        "adequacy": [],
        "fluency": [],
        "terminology": [],
        "critical_errors": 0,
        "human_rated": 0,
    }


def _add_transcription(
    values: dict[str, object], reference: str, candidate: str
) -> None:
    reference_words = _words(reference)
    candidate_words = _words(candidate)
    reference_characters = list(_characters(reference))
    candidate_characters = list(_characters(candidate))
    values["word_edits"] = int(values["word_edits"]) + _edit_distance(
        reference_words, candidate_words
    )
    values["reference_words"] = int(values["reference_words"]) + len(reference_words)
    values["character_edits"] = int(values["character_edits"]) + _edit_distance(
        reference_characters, candidate_characters
    )
    values["reference_characters"] = int(values["reference_characters"]) + len(
        reference_characters
    )


def _add_latency(values: dict[str, object], latency: object) -> None:
    if not isinstance(latency, dict):
        return
    for source_key, destination in (
        ("first_partial", "first_partial_ms"),
        ("final", "final_ms"),
    ):
        value = latency.get(source_key)
        if isinstance(value, int) and value >= 0:
            cast_values = values[destination]
            assert isinstance(cast_values, list)
            cast_values.append(value)


def _add_human_scores(values: dict[str, object], human: object) -> None:
    if not isinstance(human, dict):
        return
    scored = False
    for key in ("adequacy", "fluency", "terminology"):
        value = human.get(key)
        if isinstance(value, (int, float)) and 1 <= value <= 5:
            cast_values = values[key]
            assert isinstance(cast_values, list)
            cast_values.append(float(value))
            scored = True
    if scored:
        values["human_rated"] = int(values["human_rated"]) + 1
    if human.get("critical_error") is True:
        values["critical_errors"] = int(values["critical_errors"]) + 1


def _finalize(values: dict[str, object]) -> dict[str, object]:
    samples = int(values["samples"])
    words = int(values["reference_words"])
    characters = int(values["reference_characters"])
    human_rated = int(values["human_rated"])
    return {
        "samples": samples,
        "transcription_wer": _ratio(int(values["word_edits"]), words),
        "transcription_cer": _ratio(int(values["character_edits"]), characters),
        "translation_chrf": _mean(values["translation_chrf"]),
        "latency_ms": {
            "first_partial_p50": _percentile(values["first_partial_ms"], 0.50),
            "first_partial_p95": _percentile(values["first_partial_ms"], 0.95),
            "final_p50": _percentile(values["final_ms"], 0.50),
            "final_p95": _percentile(values["final_ms"], 0.95),
        },
        "human": {
            "rated_samples": human_rated,
            "adequacy_mean": _mean(values["adequacy"]),
            "fluency_mean": _mean(values["fluency"]),
            "terminology_mean": _mean(values["terminology"]),
            "critical_error_rate": _ratio(int(values["critical_errors"]), human_rated),
        },
    }


def _words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[\w]+(?:['’][\w]+)?", normalized, flags=re.UNICODE)


def _characters(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _edit_distance(left: Sequence[T], right: Sequence[T]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def _chrf(reference: str, candidate: str) -> float:
    reference_value = _characters(reference)
    candidate_value = _characters(candidate)
    scores: list[float] = []
    for width in range(1, 7):
        reference_ngrams = _ngrams(reference_value, width)
        candidate_ngrams = _ngrams(candidate_value, width)
        overlap = sum(
            min(count, candidate_ngrams.get(value, 0))
            for value, count in reference_ngrams.items()
        )
        precision = _ratio(overlap, sum(candidate_ngrams.values())) or 0.0
        recall = _ratio(overlap, sum(reference_ngrams.values())) or 0.0
        scores.append(
            0.0
            if precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )
    return sum(scores) / len(scores)


def _ngrams(value: str, width: int) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for index in range(max(0, len(value) - width + 1)):
        result[value[index : index + width]] += 1
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _mean(values: object) -> float | None:
    assert isinstance(values, list)
    return None if not values else round(sum(values) / len(values), 6)


def _percentile(values: object, percentile: float) -> int | None:
    assert isinstance(values, list)
    if not values:
        return None
    ordered = sorted(int(value) for value in values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


if __name__ == "__main__":
    main()
