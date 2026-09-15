# 0009 — The "zero keys" uvicorn run still needs a valid Gemini key

**Date:** 2026-09-16
**Area:** backend / local dev

## What happened
Trying to eyeball a dashboard change with the command README.md and CLAUDE.md both advertise as the
zero-key demo run:

```
SESSION_SINK=local ENABLE_SIM=1 MARKETING_RADAR_OFFLINE=1 uv run uvicorn api.index:app --port 8000
```

the server died during startup:

```
ValueError: No API key was provided. Please pass a valid API key.
```

and, with a placeholder key set, died differently:

```
google.genai.errors.ClientError: 400 INVALID_ARGUMENT ... API_KEY_INVALID
```

## Root cause
`api/index.py:56` builds the voice engine's answerer with
`GenAiAnswerTransport.resolve(config.gemini_api_key)`, and `resolve()` (`services/voice/answerer.py:34`)
constructs a real `genai.Client` and calls `client.models.list()` *at startup* to pick a model. There is
no offline branch on that path: `MARKETING_RADAR_OFFLINE` only reaches the marketing agent's backend,
and `SESSION_SINK=local` only decides where sessions are written. The whole test suite is green because
`tests/dashboard_helpers.py` injects `FakeAnswerTransport` and never goes through `build_engine`'s
default — so nothing catches the gap.

## Fix
Nothing yet — flagged, not fixed, because it is outside the change that hit it. For a browser check of a
dashboard change, build the same app the tests build and serve that instead:

```python
import pathlib, tempfile, uvicorn
from tests.dashboard_helpers import dashboard_app
app, ctx, sink = dashboard_app(pathlib.Path(tempfile.mkdtemp()), days=30)
uvicorn.run(app, port=8080)
```

The real fix belongs in `build_engine`: fall back to a fake answerer when `gemini_api_key` is empty (or
when an explicit offline flag is set), the way the tests already do.

## How to avoid next time
A documented "runs with zero keys" command needs a test that actually boots it. Until `build_engine` has
an offline branch, treat the README command as needing a live Gemini key, and use the test-built app for
front-end checks. See also [plans/0014-period-selector-overview-and-analytics.md](../plans/0014-period-selector-overview-and-analytics.md),
the work that hit this.
