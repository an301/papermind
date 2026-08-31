# Frontend ↔ backend contract

- `POST /api/session` — open a paper, get a `session_id`
- `PATCH /api/session/{id}` — scroll position (debounced; it's a hint, not a command)
- `POST /api/chat` — user message; responds `text/event-stream`

The chat stream carries two kinds of event on one channel, so every event is
typed. Agent-loop progress (`tool_start`, `tool_result`, `concept_check`) drives
the "what is it doing" indicator; `token` events are the final explanation.
Rendering them into the same bubble is the bug to avoid.
