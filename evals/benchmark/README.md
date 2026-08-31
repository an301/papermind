# Benchmark (150 questions)

One JSONL record per question. Proposed fields:

```json
{
  "id": "q001",
  "paper_id": "arxiv:1706.03762",
  "question": "...",
  "expertise_level": "beginner",
  "expected_concepts": ["attention", "softmax"],
  "expected_prerequisite_gaps": ["dot-product-similarity"],
  "expected_sections": ["3.2"],
  "notes": "..."
}
```

Keep the split by expertise level roughly even, and record *why* a question was
included -- a benchmark whose intent is undocumented rots within a month.

TODO: author `questions.jsonl`.
