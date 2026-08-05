# search_input event contract

`search_input(...)` (see `__init__.py`) returns `None` until the frontend
first reports a value, then a dict shaped exactly like this on every
subsequent call:

```json
{
  "type": "typing" | "submit" | "select",
  "query": "<current or selected text>",
  "event_id": "<unique per event>",
  "asin": "<product asin, or null>"
}
```

- `typing` — debounced (config `debounce_ms`) as the user types. Never
  triggers a search; only meant to refresh the suggestion list.
- `submit` — Enter pressed with no suggestion row highlighted. `asin` is
  always `null`.
- `select` — a suggestion was clicked, or Enter pressed while a row was
  keyboard-highlighted. `query` is the suggestion's title, `asin` its
  product code (may be `""`).

`event_id` changes on every event, including repeats of the same query text
— this guarantees Streamlit always reruns (it dedupes identical component
values otherwise) and lets `app.py` detect "have I already processed this
exact event" via a simple equality check against the last seen `event_id`.

This contract is the stable surface between the frontend and `app.py`. The
current `frontend/index.html` is a build-step-free vanilla JS implementation
(same approach as `st_keyup`). If it is ever replaced with a proper React
build, only `frontend/` changes — this contract, `__init__.py`, and every
caller in `app.py` stay the same.
