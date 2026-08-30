# Provider quality evaluation

Provider support in the Gateway does not automatically select a provider for
Classroom or any other product. Classroom ADR 0004 separately authorizes a
reversible Gemini pilot. Evaluation remains open and still determines whether
that mapping should become permanent.

Use a consented, non-production test corpus representing classroom speech:

- quiet lecture, room echo, background conversation, and device movement;
- slow and fast speakers, accents, code-switching, and long pauses;
- English, Simplified Chinese, German, and Spanish;
- course terminology from science, mathematics, humanities, and professional
  subjects;
- numbers, equations, names, abbreviations, and negation where mistakes have
  high study impact.

Run incumbent and candidate pipelines over identical audio. Randomize them as
`candidate_a`, `candidate_b`, and so on before human review. Do not reveal the
provider until ratings are locked.

Each private JSONL record has this shape:

```json
{
  "sample_id": "opaque-identifier",
  "reference": {
    "transcript": "Manually corrected reference text.",
    "translations": {"de-DE": "Human reference translation."}
  },
  "candidates": {
    "candidate_a": {
      "transcript": "Candidate transcript.",
      "translations": {"de-DE": "Candidate translation."},
      "latency_ms": {"first_partial": 350, "final": 1200},
      "human": {
        "adequacy": 5,
        "fluency": 5,
        "terminology": 4,
        "critical_error": false
      }
    }
  }
}
```

Store audio, references, candidates, and the provider-to-candidate mapping only
under `evaluation/private/`; that directory is excluded from Git. Aggregate
metrics contain no transcript or translation text:

```powershell
python -m scripts.evaluate_quality evaluation/private/results.jsonl
```

WER and CER evaluate transcription. chrF is a supporting translation metric,
not the product decision: translation must also receive blinded 1–5 human
ratings for adequacy, fluency, and subject terminology, plus a critical-error
flag. Measure first-partial and finalized-text latency from audio capture time.
