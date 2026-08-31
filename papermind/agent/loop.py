"""The ReAct loop. This is the heart of the project -- keep it readable.

Shape:

    while True:
        response = await anthropic.messages.create(...)   # Reason
        if response.stop_reason != "tool_use":
            break
        results = await run_tools(response)               # Act
        messages.append(tool_results(results))            # Observe

Deliberately hand-written. The whole point of PaperMind is that the control
flow is inspectable, so: no LangChain, no LlamaIndex, no agent framework, and
no SDK tool-runner helper either -- the loop stays in this file where it can be
read top to bottom and stepped through in a debugger.

Two hard rules, both of which exist to protect the prompt cache:

1. The tool list is built ONCE at startup and sorted deterministically. Tools
   render at prefix position 0, so adding, dropping, or reordering a tool
   mid-conversation invalidates the entire cache. MCP servers hand back tools
   in connection-completion order, which is racy -- sort before use.
2. Nothing volatile (timestamps, session ids, the user's current expertise
   level) goes into the system prompt. Expertise changes as KNOWS edges are
   written, and interpolating it into `system` would invalidate every cached
   turn behind it. Inject it as a mid-conversation `{"role": "system"}` message
   instead -- supported on Opus 5, and it sits after the cached history.

Termination: bound the loop by iteration count AND by a token budget. A
prerequisite-gap chase can otherwise walk the concept graph indefinitely.
"""

# TODO: run_loop(session, user_message) -> AsyncIterator[AgentEvent]
