"""Where the four `cache_control` breakpoints go, and why.

The Anthropic API allows at most 4 breakpoints per request, and caching is a
prefix match -- one changed byte invalidates everything after it. Render order
is tools -> system -> messages. Our four, from most to least stable:

    1. end of `system`          -- covers tools + system prompt (frozen)
    2. end of the paper content -- stable for the whole session (this is the
                                   big one; a full paper is most of the prompt)
    3. end of the prior turn    -- the conversation history so far
    4. (spare) end of retrieved -- large retrieved sections, when a turn pulls
                                   enough context to be worth a write

TTL: the 5-minute default fits an active reading session, where turns arrive
minutes apart. Use `ttl: "1h"` only for the paper-content breakpoint, and only
if measurements show users idling longer than 5 minutes mid-paper -- 1h TTL
doubles the write cost (2x vs 1.25x) and needs 3+ reads to break even.

Verify with `usage.cache_read_input_tokens`. If it is 0 across consecutive
turns, something upstream is mutating the prefix; treat that as a bug, not a
tuning problem.
"""
