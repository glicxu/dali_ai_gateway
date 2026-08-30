from __future__ import annotations

import json

from scripts.evaluate_quality import evaluate


def test_quality_evaluation_aggregates_blinded_metrics(tmp_path) -> None:
    path = tmp_path / "private-results.jsonl"
    path.write_text(
        json.dumps(
            {
                "sample_id": "sample-1",
                "reference": {
                    "transcript": "Energy is conserved.",
                    "translations": {"de-DE": "Energie bleibt erhalten."},
                },
                "candidates": {
                    "candidate_a": {
                        "transcript": "Energy is conserved.",
                        "translations": {"de-DE": "Energie bleibt erhalten."},
                        "latency_ms": {"first_partial": 300, "final": 900},
                        "human": {
                            "adequacy": 5,
                            "fluency": 5,
                            "terminology": 5,
                            "critical_error": False,
                        },
                    },
                    "candidate_b": {
                        "transcript": "Energy was conserved.",
                        "translations": {"de-DE": "Energie erhalten."},
                        "latency_ms": {"first_partial": 500, "final": 1400},
                        "human": {
                            "adequacy": 3,
                            "fluency": 4,
                            "terminology": 3,
                            "critical_error": True,
                        },
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = evaluate(path)

    assert result["sample_count"] == 1
    candidate_a = result["candidates"]["candidate_a"]
    assert candidate_a["transcription_wer"] == 0
    assert candidate_a["translation_chrf"] == 1
    assert candidate_a["latency_ms"]["final_p95"] == 900
    assert candidate_a["human"]["critical_error_rate"] == 0
    candidate_b = result["candidates"]["candidate_b"]
    assert candidate_b["transcription_wer"] > 0
    assert candidate_b["human"]["critical_error_rate"] == 1
