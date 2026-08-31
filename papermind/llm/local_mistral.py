"""Local fine-tuned Mistral 7B: generates the final explanation.

Why the split: the hosted model decides *what* to explain (which sections,
which prerequisite gaps); this model decides *how* to say it, tuned on our own
explanation style at three expertise levels. Reasoning quality needs a frontier
model; voice does not.

The async problem, stated plainly: `transformers` generation is blocking,
CPU/GPU-bound, and holds the GIL. Calling it directly from a coroutine stalls
the entire event loop -- every other session's SSE stream freezes with it.
So:

- Run generation via `asyncio.to_thread` (or a dedicated process for real
  isolation), never inline.
- Guard it with a semaphore sized to the GPU, typically 1. There is one set of
  weights in VRAM; concurrent generation either OOMs or thrashes. Queue instead.
- Load lazily on first use and keep the handle. Cold load is tens of seconds.
- Stream tokens out via `TextIteratorStreamer` so the SSE channel stays live.

If no GPU is present, fall back to the hosted model and say so in the trace --
a silent fallback makes eval numbers meaningless.
"""
