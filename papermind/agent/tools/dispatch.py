"""Executes tool calls the model requested and formats results back as tool_result blocks.

Runs the calls in a turn concurrently (the API may emit several tool_use blocks
in one response) and returns ALL results in a single user message -- splitting
them across messages teaches the model to stop calling tools in parallel.

Failures come back as `tool_result` with `is_error: true`, never as a dropped
block or a raised exception: the model recovers from a described failure, but a
missing tool_result is a malformed conversation.
"""
