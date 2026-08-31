"""The single conversational endpoint: user message in, explanation stream out.

Streams over SSE. Two things stream on the same channel and the frontend needs
to tell them apart, so events are typed: agent-loop progress (which tool is
running, which concepts were checked) versus final explanation tokens from the
local model. Users wait several seconds while the agent retrieves and checks
prerequisites; showing that work is the difference between "thinking" and "hung".
"""
