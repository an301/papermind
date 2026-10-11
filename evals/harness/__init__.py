"""The eval harness: code that loops over the benchmark, runs the system, scores and logs.

    dataset.py    BenchmarkRow, load_benchmark (labels checked against paper.json)
    retrieval.py  the retrieval suite: BM25 / dense / hybrid recall@k, no API key
    runner.py     the agent suite: real AgentRunner + MCP servers, headless, per-row results
    scorers.py    pure scoring: recall@k, token F1, percentiles, calibration, Cohen's kappa
    judges.py     LLM judges: faithfulness, blind level, citation precision, uncited paragraphs
    report.py     summaries and tables (always sliced by level); re-renders saved runs
    wandb_log.py  W&B logging, offline by default, a no-op when disabled
    run.py        the CLI: python -m evals.harness.run --suite retrieval|agent|latency|agreement
"""
