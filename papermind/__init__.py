"""PaperMind — an AI research-paper reading agent.

Single import root for the whole application: the FastAPI service, the agent
loop, the stores, and the ingestion pipeline all live under this package so
`python -m papermind.ingest` and `uvicorn papermind.main:app` share one namespace.
"""
