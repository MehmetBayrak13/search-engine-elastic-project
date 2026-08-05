# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit search UI (`app.py`) over an Elasticsearch index of Amazon product
metadata, plus standalone one-off scripts that import the Amazon Reviews 2023
dataset from Hugging Face into that Elasticsearch cluster. There is no test
suite, build step, or package manifest beyond `requirements.txt`. UI text,
comments, and log messages throughout the codebase are in Turkish.

## Commands

```bash
pip install -r requirements.txt   # streamlit, requests
streamlit run app.py              # run the search app (needs env vars below)
python full_amazon_importer.py    # run/resume the full dataset import (needs different env vars)
python index_amazon.py            # legacy: import a small (10/category) sample into amazon-products-v1
python inspect_amazon.py          # print a few raw records from one category's parquet file, no ES needed
```

No linter, formatter, or test runner is configured in this repo.

`full_amazon_importer.py` and `index_amazon.py` depend on `datasets`,
`elasticsearch`, and `huggingface_hub`, which are **not** in
`requirements.txt` (that file only covers the Streamlit app). Install them
separately if working on the importers.

### Required environment variables

Two unrelated Elasticsearch auth schemes are in play — don't confuse them:

- **`app.py`** (raw REST calls via `requests`): `ELASTICSEARCH_URL`,
  `ELASTICSEARCH_API_KEY`.
- **`full_amazon_importer.py` / `index_amazon.py`** (official `elasticsearch`
  client): `ELASTIC_CLOUD_ID`, `ELASTIC_API_KEY`.

## Architecture

### Search app (`app.py` + `services/` + `components/`)

`app.py` is the Streamlit UI/session_state orchestration layer only. Query
building, HTTP calls to Elasticsearch, intent detection, translation
expansion, and dynamic category discovery live in `services/search_service.py`
and `services/autocomplete_service.py` — neither imports Streamlit or touches
`session_state`, so the same functions could be called from a future FastAPI
endpoint without change. Shared JSON-safe types (`SearchResult`,
`SuggestionItem`, `PaginationLimitError`) live in `services/search_models.py`.
Caching (`st.cache_data`) is a Streamlit concern and stays in `app.py`: the
service functions accept a `fetch_aggregations`/`fetch_hits` dependency-injection
parameter, defaulting to an uncached real Elasticsearch call; `app.py` injects
its own cached wrapper for the real UI call path. `app.py` still re-exports
the services' top-level functions by name (e.g. `app.build_search_query(...)`
works) for test/back-compat convenience, but always calls through the
qualified `search_service.X(...)` / `autocomplete_service.X(...)` form in
`main()` so the cached fetchers are actually used.

The search box and its autocomplete dropdown are a single Streamlit custom
component, `components/search_input/` (buildless static HTML/JS frontend +
a thin Python wrapper). It contains no search/autocomplete business logic —
it only emits generic `{type: "typing"|"submit"|"select", query, event_id,
asin}` events over Streamlit's standard component protocol (see
`components/search_input/CONTRACT.md`), which `app._handle_search_input_event`
turns into the same `_trigger_explicit_search`/`_select_query` state
transitions the "Ara" button uses.

- **Two separate indices, two separate purposes.** Full-text search hits
  `amazon-products-000001,amazon-products-000002` (`INDEX_NAME`). Live
  type-ahead hits a *different* index,
  `amazon-products-autocomplete-000001,amazon-products-autocomplete-000002`
  (`AUTOCOMPLETE_INDEX_NAME`), which has an Edge NGram analyzer on
  `title.autocomplete`. The two queries are built by separate functions
  (`build_search_query` vs `build_autocomplete_query`) and are independent:
  toggling off every lexical search method in the sidebar does not affect
  live suggestions, and vice versa.
- **Index mappings/settings live only in the cluster, not in this repo.**
  They were created through Kibana Dev Tools (see the comment in
  `index_amazon.py`). If you need to know what analyzers/fields exist,
  you have to inspect the cluster — there's no mapping JSON checked in here.
- **Query shape convention**: lexical match methods (exact ASIN `term`,
  `match_phrase`, multi-field `multi_match`, fuzzy `multi_match`) are combined
  in a `bool.should` with `minimum_should_match: 1` nested inside `bool.must`,
  so at least one lexical method must match. Intent-boost queries go in the
  outer `bool.should` (rerank-only, cannot produce a hit on their own), and
  intent exclusions go in `bool.must_not`. This must/should/must_not split is
  intentional — don't move intent-boost logic into `must` or it will start
  gating results instead of just reranking them.
- **Intent detection** (`INTENT_RULES`, `detect_search_intent`,
  `_build_intent_signals`) is a small rule table keyed by intent name (only
  `"watch"` is implemented). Each rule has trigger terms, a
  cancel-if-query-contains list (e.g. "watch book" suppresses the book
  exclusion), category boost terms, and negative categories to exclude. To
  add a new intent, add an entry to `INTENT_RULES` — the query-building code
  is generic over the rule table.
- **Session-state driven control flow**, not a linear script: search
  results, the active query, and the widget "version" are all stashed in
  `st.session_state` so reruns (triggered by suggestion clicks, example-query
  chips, or the Ara button) don't lose state. The search input's widget
  `key` is versioned (`search_box_v{N}`) specifically to let code
  programmatically overwrite the box's value without hitting Streamlit's
  duplicate-widget-id rules. If results exist for a stale combination of
  sidebar toggles (`search_sig` mismatch), they're discarded rather than
  shown under the wrong settings.
- **Product cards are rendered as raw HTML** via
  `components.html(...)` (not `st.markdown`) so the whole card can be one
  clickable `onclick` region that opens `amazon.com/dp/<asin>` in a new tab.
  All interpolated values go through `html.escape` first.
- The `streamlit-keyup` dependency and the JS "bridge" hack it required to
  make Enter reliable were removed entirely (that package's single/only
  release, 0.3.0, can't distinguish Enter from any other keystroke, which is
  what forced the old bridge to exist). The `components/search_input` custom
  component replaces it — see above.

### Import scripts

- **`full_amazon_importer.py`** is the real, resumable importer — it streams
  every `raw_meta_*` Parquet file for every category in the
  `McAuley-Lab/Amazon-Reviews-2023` HF dataset (pinned to
  `DATASET_REVISION`) and bulk-indexes into the `amazon-products` **alias**
  (not a literal index name — it expects that alias to already exist with
  exactly one write index; see `validate_elasticsearch`). It's designed to
  run for hours/days unattended:
  - Progress is durable via `amazon_import_checkpoint.json`
    (completed files, current file, next row offset, running totals),
    written atomically (write to `.tmp`, then `Path.replace`). Re-running the
    script resumes from this file; delete/edit it only if you mean to
    reprocess data.
  - Per-row/per-file errors go to `import_errors.jsonl`, not the checkpoint —
    failures don't halt the run.
  - Human-readable progress goes to `import_progress.log` (append-only,
    grows unbounded across runs).
  - `transform_product` is deliberately category-agnostic — no per-category
    special-casing — so the same function normalizes phones, books,
    appliances, etc. uniformly into the target document shape (adds
    `categories_text` and `source_category`, which the older
    `index_amazon.py` transform does not emit).
- **`index_amazon.py`** is an older/simpler one-shot sample importer (10
  products per category, into `amazon-products-v1`, a literal index that
  must already exist). Its `transform_product` is a near-duplicate of the
  one in `full_amazon_importer.py` but is missing `categories_text` /
  `source_category` — keep that in mind if you're tempted to unify them.
- **`inspect_amazon.py`** is a standalone debugging script with no
  Elasticsearch dependency at all — just prints a few raw HF dataset records
  to stdout to check field shapes before writing transform logic.

### Generated/local-only files (not meant to be hand-edited)

`amazon_import_checkpoint.json`, `import_progress.log`, and
`import_errors.jsonl` are runtime output of `full_amazon_importer.py`, kept
in the working tree for resumability, not source.

---

# Amazon Elasticsearch Product Search — Project Instructions

## 1. Project Overview

This repository contains a Streamlit-based product search engine backed by
Elasticsearch / Elastic Cloud.

The product dataset is primarily English, but the application must support:

- English queries
- Turkish queries
- Turkish morphological variations
- Turkish-to-English product query expansion
- Exact ASIN search
- Phrase search
- Multi-field search
- Fuzzy search
- Edge NGram autocomplete
- Dynamic category discovery
- Optional manual intent overrides

Main project files:

- `app.py`
- `config.py`
- `services/search_service.py`
- `services/autocomplete_service.py`
- `services/search_models.py`
- `components/search_input/`
- `config/search_config.json`
- `config/intent_rules.json`
- `config/query_translations.json`
- `tests/`
- `requirements.txt`
- `README.md`
- `.gitignore`

The application is deployed with Streamlit Community Cloud and connected to
Elastic Cloud.

---

## 2. General Working Rules

When the user asks for a feature, bug fix, refactor, UI improvement, test,
configuration change, search improvement, or documentation update:

1. Inspect all relevant files before making changes.
2. Read the existing implementation completely enough to understand its behavior.
3. Do not only explain the solution.
4. Apply the changes directly to the local project files.
5. Preserve existing working behavior unless removal is explicitly requested.
6. Update tests when behavior changes.
7. Update configuration validation when config structure changes.
8. Update `README.md` when setup, architecture, configuration, search behavior,
   deployment, reindexing, or usage changes.
9. Run syntax checks and tests after making changes.
10. Fix failures caused by the changes.
11. Report any manual action still required from the user.
12. Never claim that a command passed unless it was actually executed.

Do not ask the user to paste files that already exist in this repository.
Read them directly.

Do not provide only example snippets when the user asks to implement something.
Modify the actual files.

---

## 3. Required Validation

After relevant Python changes, run:

```bash
python -m py_compile app.py
python -m py_compile config.py
python -m pytest -q
```

When useful, also run a lightweight application check:

```bash
python -c "import app; print('app import successful')"
```

A Streamlit startup check may also be used when appropriate, but do not leave a
background server running unnecessarily.

If a validation command fails:

1. Read the full error.
2. Fix the cause.
3. Run the command again.
4. Do not hide or ignore failures.

At the end, report:

- Files changed
- Main behavior changes
- Tests and checks executed
- Test result
- Remaining manual Elasticsearch steps
- Remaining deployment steps

---

## 4. Git Safety

Local file editing and local test execution are allowed.

Do not automatically perform any of the following unless the user explicitly
requests and approves it:

- `git push`
- force push
- destructive reset
- branch deletion
- commit history rewriting
- GitHub repository deletion
- automatic production deployment
- secret rotation
- production index deletion
- production index closing
- production alias switching
- large production reindexing

Safe read-only Git commands may be used:

```bash
git status
git diff
git log
```

A local commit may only be created when explicitly requested.

Before any consequential Git action, explain:

- The exact command
- What it changes
- Whether it can be reversed

---

## 5. Secrets and Security

Never write secrets into:

- Python source code
- JSON configuration
- tests
- README
- logs
- exception messages
- Git history
- screenshots
- UI output

Sensitive values include:

- `ELASTICSEARCH_URL`
- `ELASTICSEARCH_API_KEY`
- Streamlit Secrets
- authentication tokens
- deployment credentials
- private endpoints

Secrets must only come from:

1. Environment variables
2. Streamlit Secrets

Expected secret keys:

```text
ELASTICSEARCH_URL
ELASTICSEARCH_API_KEY
```

`.streamlit/secrets.toml` must remain excluded from Git.

Do not print, log, serialize, expose through `repr`, or show secret values.

Do not introduce:

- `eval`
- arbitrary Python execution
- arbitrary shell execution from user input
- arbitrary Elasticsearch Query DSL from user input
- arbitrary field names from user input
- arbitrary index names from user input
- unsafe HTML generated from unescaped user input

Continue using `html.escape` where user-controlled or Elasticsearch-derived text
is inserted into HTML.

---

## 6. Configuration Architecture

Values that represent deployment settings, search tuning, limits, labels, or UI
configuration should come from:

1. Environment variables
2. Streamlit Secrets
3. JSON configuration
4. Safe defaults

Current configuration files:

```text
config/search_config.json
config/intent_rules.json
config/query_translations.json
```

`config.py` must:

- Load configuration safely
- Validate configuration types
- Reject invalid negative limits or timeouts
- Reject empty required field lists
- Reject invalid boost values
- Reject malformed translation rules
- Reject malformed intent overrides
- Return immutable or effectively immutable structured configuration
- Avoid leaking secrets
- Produce understandable `ConfigError` messages

Do not add an unnecessary configuration framework when standard library
facilities are sufficient.

Preferred tools:

- `json`
- `dataclasses`
- `pathlib`
- `os`
- standard Python typing

Configuration changes do not need runtime hot reload.

The expected behavior is:

- Local config change → restart Streamlit
- GitHub config change → push commit → Streamlit redeploys automatically
- Runtime users cannot edit configuration from the UI

Do not build an admin panel for editing search configuration.

---

## 7. Values That Should Be Configurable

The following values should not remain unnecessarily hardcoded in `app.py`:

### Elasticsearch

- Normal search index list
- Autocomplete index list
- Normal timeout
- Autocomplete timeout
- `track_total_hits` settings

### Limits

- Normal result limit
- Autocomplete Elasticsearch fetch limit
- Autocomplete display limit
- Autocomplete minimum character count

### Search behavior

- Exact ASIN field and boost
- Phrase field and boost
- Multi Match type
- Multi Match operator
- Multi Match fields and field boosts
- Multi Match global boost
- Fuzzy type
- Fuzziness
- Fuzzy prefix length
- Fuzzy max expansions
- Fuzzy fields and field boosts
- Fuzzy global boost
- Autocomplete field
- Autocomplete operator

### Dynamic category discovery

- Enabled state
- Cache TTL
- Minimum query length
- Maximum category candidates
- Aggregation size
- Search fields and boosts
- Aggregation fields
- Category boost
- Timeout

### Cross-language search

- Enabled state
- Autocomplete enabled state
- Translation file location
- Source language
- Target language
- Maximum translation variants
- Original query boost
- Normalized query boost
- Phrase translation boost
- Token translation boost
- Minimum query length
- Cache TTL

### UI

- Hero title
- Hero subtitle
- Hero badge
- Search placeholder
- Placeholder image
- Example queries
- Debounce duration
- Button labels
- Sidebar labels
- Toggle descriptions
- Chip labels
- Empty-state text
- Warning messages
- Error messages
- Result summary templates
- Suggestion text
- Status text
- Intent fallback icon

### Other

- Elasticsearch source fields
- Amazon product URL template

---

## 8. Values That Must Remain Controlled in Code

Do not make the following directly configurable through user input or raw JSON
execution:

- `bool`
- `must`
- `should`
- `must_not`
- `term`
- `match`
- `match_phrase`
- `multi_match`
- `match_none`
- `minimum_should_match`
- safe HTTP error handling
- HTML escaping
- Streamlit state transitions
- API authentication header construction
- exact ASIN protection
- query security boundaries

The application may generate these query structures from validated config, but
users must not be allowed to submit arbitrary raw Elasticsearch queries.

---

## 9. Existing Search Features That Must Be Preserved

Unless explicitly requested otherwise, preserve:

- Exact ASIN priority
- Phrase matching
- Multi-field matching
- Fuzzy matching
- Edge NGram autocomplete
- Dynamic category discovery
- Optional manual intent overrides
- Turkish query processing
- English query processing
- Turkish-to-English query expansion
- Streamlit state behavior
- Suggestion deduplication
- Amazon links
- Product cards
- API error handling
- Cached suggestions
- Cached category discovery
- Search method switches

The lexical product match must remain mandatory.

Correct structure:

```text
bool.must
└── lexical query group
    ├── phrase
    ├── multi_match
    ├── fuzzy
    └── exact ASIN
```

Category discovery and intent signals must be used only for reranking or explicit
safe exclusions:

```text
bool.should
├── dynamic category boosts
├── multilingual query boosts
└── optional manual override boosts
```

Manual exclusions may be placed under:

```text
bool.must_not
```

Dynamic category discovery must never independently return products without a
lexical match.

---

## 10. Elasticsearch Indexes

Normal search indexes:

```text
amazon-products-000001
amazon-products-000002
```

Production autocomplete indexes:

```text
amazon-products-autocomplete-000001
amazon-products-autocomplete-000002
```

Do not switch production autocomplete back to:

```text
amazon-products-autocomplete-test
```

Index names should come from validated configuration, not from scattered
hardcoded constants.

Before using both autocomplete indexes, confirm that they exist and contain the
expected document counts.

---

## 11. Turkish Morphology and Stemming

Turkish morphology and Turkish-to-English translation are separate problems.

### Turkish stemming problem

Examples:

```text
telefon
telefonlar
telefonların
telefonlarda
telefonlardan
telefonu
```

These forms should be normalized using Elasticsearch's general Turkish stemmer,
not by manually listing every suffix variation.

Do not use `stemmer_override` for ordinary Turkish suffix variations such as:

```text
telefonlar, telefonların, telefonu => telefon
kitaplar, kitapların => kitap
kulaklıklar, kulaklıkların => kulaklık
```

Use an Elasticsearch stemmer filter similar to:

```json
{
  "type": "stemmer",
  "language": "turkish"
}
```

Preferred analyzer filter order:

```text
apostrophe
turkish lowercase
product-specific stemmer override
general Turkish stemmer
```

`stemmer_override` should only remain for genuine exceptions such as:

- Product-domain words incorrectly stemmed
- Important technical terms
- Brand-specific exceptions
- Terms that need a controlled canonical form

Do not assume the stemmer output will always be a dictionary root. It may produce
an algorithmic stem.

### Multi-fields

Prefer preserving separate representations:

```text
title
title.tr
title.autocomplete
title.keyword
```

Recommended responsibilities:

- `title`: regular, minimally transformed full-text search
- `title.tr`: Turkish analyzer and Turkish stemming
- `title.autocomplete`: Edge NGram autocomplete
- `title.keyword`: exact value behavior where needed

Use the Turkish analyzer only on appropriate `.tr` fields such as:

- `title.tr`
- `categories_text.tr`
- `description.tr`
- `features.tr`

Do not damage:

- Brands
- Model names
- ASIN values
- Product codes
- Technical identifiers

### Reindex requirement

Analyzer and mapping changes do not retroactively affect indexed documents.

Adding or changing the Turkish stemmer requires:

1. A new test index
2. `_analyze` tests
3. Mapping validation
4. Small sample testing
5. A new production index
6. Reindexing
7. Count validation
8. Search comparison
9. Alias or application cutover

Never start a large reindex without explicit user approval.

---

## 12. Turkish-to-English Query Expansion

The dataset is primarily English.

A Turkish user must still be able to retrieve correct English products.

Examples:

```text
kablosuz kulaklık
→ wireless headphones
→ wireless headset

oyuncu faresi
→ gaming mouse

telefon şarj aleti
→ phone charger
→ cell phone charger

kahve öğütücü
→ coffee grinder

tuvalet kağıdı
→ toilet paper
→ bath tissue

kedi maması
→ cat food

akıllı saat
→ smartwatch
→ smart watch

hava filtresi
→ air filter

telefon kılıfı
→ phone case
→ cell phone case
```

A Turkish stemmer does not translate meanings.

For example:

```text
kulaklık → headphones
kablosuz → wireless
```

requires cross-language query expansion.

### Translation storage

Translations must not be hardcoded in `app.py`.

Use:

```text
config/query_translations.json
```

Expected structure:

```json
{
  "phrases": {
    "kablosuz kulaklık": [
      "wireless headphones",
      "wireless headset"
    ],
    "oyuncu faresi": [
      "gaming mouse"
    ]
  },
  "terms": {
    "kablosuz": [
      "wireless"
    ],
    "kulaklık": [
      "headphones",
      "headset"
    ]
  }
}
```

Phrase matches must take priority over token-level translation.

The dictionary must support:

- One Turkish phrase → multiple English alternatives
- One Turkish word → multiple English alternatives
- Deduplication
- Maximum variant limits
- Safe fallback for unknown words
- Empty dictionary operation without crashing

### Query expansion pipeline

A multilingual query pipeline should preserve:

- Original query
- Normalized query
- Phrase translations
- Token translations
- Exact identifiers
- Fallback behavior

Example:

```json
{
  "original_query": "kablosuz kulaklıklar",
  "normalized_query": "kablosuz kulaklık",
  "phrase_translations": [
    "wireless headphones",
    "wireless headset"
  ],
  "expanded_queries": [
    "kablosuz kulaklıklar",
    "kablosuz kulaklık",
    "wireless headphones",
    "wireless headset"
  ]
}
```

The original query must never be discarded.

Unknown Turkish queries must still run using the original query.

Do not translate or stem:

- ASINs
- Brand names when identifiable
- Model identifiers
- Product codes
- Alphanumeric technical identifiers

Query-time translation does not require reindexing.

Ingest-time document translation is not the default approach because millions of
documents would need translation, storage would increase, and another large
reindex would be required.

---

## 13. Query Expansion Architecture

Prefer a testable design such as:

```text
QueryExpansionResult
TranslationProvider
DictionaryTranslationProvider
```

A dictionary provider should be implemented first.

Do not add a paid external translation dependency by default.

An optional provider abstraction may be designed for future use, but avoid
unnecessary frameworks.

A function similar to this should remain pure and unit-testable:

```python
expand_multilingual_query(query_text: str) -> QueryExpansionResult
```

It should not call Elasticsearch.

It may return:

- `original_query`
- `normalized_query`
- `detected_language`
- `phrase_translations`
- `token_translations`
- `expanded_queries`
- `used_translation`
- `warnings`

Do not label a value as probability or confidence unless it has a mathematically
valid interpretation.

---

## 14. Multilingual Elasticsearch Query Behavior

Normal search should search both original and translated variants.

Conceptual structure:

```text
bool.must
└── bool.should
    ├── original Turkish lexical queries
    ├── normalized Turkish lexical queries
    ├── English phrase translations
    └── English token translations
```

At least one lexical variation must match:

```text
minimum_should_match: 1
```

Translated variants should receive configuration-driven boosts.

Exact ASIN search must remain separate and unchanged.

Translated terms must not bypass the lexical match requirement.

Avoid combinatorial explosion:

- Deduplicate variants
- Limit translation count
- Prefer full phrase translations
- Restrict token combinations
- Use configuration limits

---

## 15. Autocomplete and Multilingual Search

Autocomplete must continue using Edge NGram indexes.

Do not run dynamic category discovery on every keystroke.

Dictionary-based local query expansion may be used for autocomplete when enabled,
because it does not require a second Elasticsearch request.

Autocomplete must:

- Preserve the original query
- Add safe translated alternatives where available
- Use one Elasticsearch search payload
- Keep `minimum_should_match: 1`
- Respect minimum character length
- Respect fetch and display limits
- Deduplicate suggestions
- Remain independent from normal lexical switches

Do not translate incomplete prefixes aggressively.

For example:

```text
kablo
```

must not automatically be assumed to mean:

```text
kablosuz
```

unless a validated prefix translation design explicitly supports it.

Full phrase matches such as:

```text
kablosuz kulaklık
```

may safely expand to:

```text
wireless headphones
```

---

## 16. Dynamic Category Discovery

Dynamic category discovery is the primary category-intent mechanism.

It should infer category candidates from Elasticsearch data without requiring one
manual rule per product type.

It must support previously unseen product queries such as:

```text
toilet paper
gaming mouse
cat food
coffee grinder
office chair
phone charger
```

It should use configured search fields and configured aggregation fields.

Recommended properties:

- Runs only during normal search
- Does not run on every autocomplete keystroke
- Uses a short timeout
- Uses `size: 0`
- Uses `track_total_hits: false`
- Uses limited aggregation sizes
- Caches results
- Fails safely
- Never becomes the single point of failure

If discovery fails, normal lexical search must continue.

For Turkish queries against English data, category discovery should consider:

- Original Turkish query
- Normalized Turkish query
- Highest-priority English translation variants

Example:

```text
tuvalet kağıdı
→ toilet paper
```

The English translation may be used to discover English category buckets.

Do not call normalized category score a probability.

Use honest fields such as:

- `rank`
- `doc_count`
- `normalized_score`
- `source`

---

## 17. Manual Intent Overrides

`config/intent_rules.json` is not the main intent engine.

It is an optional override layer for exceptional behavior:

- Aliases
- Forced boosts
- Safe exclusions
- Display labels
- Icons
- Priority
- Enable/disable state

The application must work when:

```json
{}
```

is the entire `intent_rules.json` file.

Do not create a manual intent rule for every product category.

A rule such as `watch` may remain only when it expresses a special business rule,
for example:

- Prefer watch categories
- Exclude books unless the query explicitly requests a book
- Show a custom intent label

Manual overrides may supplement dynamic discovery but must not replace it.

No hardcoded `watch` checks should remain in `app.py`.

Intent icon and display text should come from config.

---

## 18. User Interface Requirements

Keep Streamlit.

Do not migrate to:

- React
- Vue
- Angular
- A separate frontend application

The interface should remain:

- Professional
- Modern
- Clean
- Responsive
- Readable
- Suitable for light and dark modes
- Usable on desktop and mobile

Preserve:

- Search box
- Search button
- Sidebar controls
- Search method switches
- Live suggestions
- Product cards
- Result summary
- Empty state
- Error states
- Amazon links

Do not add excessive animation.

Do not sacrifice search correctness for visual design.

Use config-driven UI text where reasonable.

Continue escaping user and Elasticsearch text before inserting it into HTML.

Avoid repeated inline styling where a centralized style section is appropriate.

---

## 19. Streamlit State Requirements

Preserve the existing state behavior unless explicitly redesigning it.

Important state concepts include:

```text
query_widget_version
pending_value
run_search
hide_suggestions_once
current_query
search_query
search_hits
search_total
search_error
search_sig
es_ok
```

Avoid:

- Duplicate widget IDs
- Infinite rerun loops
- Suggestions repeatedly reopening after selection
- Search results disappearing on harmless reruns
- Stale results being displayed under changed settings

When search settings change, stale results should be cleared or clearly marked as
outdated.

---

## 20. Performance Requirements

Avoid unnecessary Elasticsearch requests.

Normal search may use:

1. Optional cached category discovery request
2. Main product search request

Autocomplete should normally use only:

1. One product suggestion request

Cache independently:

- Autocomplete suggestions
- Dynamic category discovery
- Local translation expansion where useful

Use configured TTL values.

Limit:

- Translation variants
- Aggregation bucket counts
- Category candidates
- Fuzzy max expansions
- Returned `_source` fields
- Suggestion count
- Result count

Do not fetch full documents when only aggregations are required.

---

## 21. Error Handling

Preserve understandable handling for:

- Connection errors
- Timeouts
- HTTP 401
- HTTP 403
- HTTP 404
- Elasticsearch errors
- Invalid JSON responses
- Missing configuration
- Missing secrets
- Missing autocomplete indexes
- Translation configuration errors

Never include API keys or secret values in error messages.

Optional systems must fail safely:

- Translation failure → use original query
- Category discovery failure → continue normal search
- Missing autocomplete indexes → disable suggestions and inform the user
- Empty intent overrides → continue normally

Do not broadly swallow errors unless there is a clear fallback and the failure is
non-critical.

Where exceptions are intentionally caught, add a clear comment explaining the
fallback.

---

## 22. Testing Requirements

Use mocked Elasticsearch calls for unit tests.

Do not require a live Elastic Cloud cluster for normal test execution.

Maintain or add tests for:

### Configuration

- Environment variables override JSON values
- Index list parsing
- Missing secrets are reported
- Secret representation is masked
- Negative timeout is rejected
- Empty search index list is rejected
- Empty required field lists are rejected
- Invalid boosts are rejected
- Invalid translation config is rejected
- Empty translation dictionary is allowed
- Empty intent rules are allowed

### Query builders

- No lexical methods returns `match_none`
- Lexical queries remain under `bool.must`
- Dynamic boosts go under `bool.should`
- Manual exclusions go under `bool.must_not`
- Fuzzy switch works
- Configured fields and boosts are used
- Autocomplete field comes from config
- Autocomplete operator comes from config
- Exact ASIN remains unchanged

### Turkish support

- Turkish morphological variants are not manually enumerated in application code
- Analyzer test commands are generated
- Brands and model codes are protected
- ASINs are not stemmed or translated

### Translation

- `kablosuz kulaklık` expands to `wireless headphones`
- `oyuncu faresi` expands to `gaming mouse`
- `tuvalet kağıdı` expands to `toilet paper`
- Phrase translations take priority
- Token fallback works
- Unknown query falls back to original
- Duplicate translations are removed
- Maximum variant limit is respected
- Original query remains in the payload
- Translation boost comes from config
- Exact identifiers are preserved
- Autocomplete expansion makes no extra Elasticsearch request

### Dynamic category discovery

- Unknown product types can produce category candidates
- Discovery uses configured aggregation fields
- Discovery respects minimum query length
- Discovery respects maximum candidate limit
- Discovery can be disabled
- Discovery failure does not break normal search
- Autocomplete does not trigger discovery
- Translated English queries can support discovery

### UI

- Labels come from config
- No hardcoded watch-specific condition remains
- No hardcoded watch icon remains
- Empty intent rules do not break UI rendering

Avoid tests that only assert internal formatting without protecting meaningful
behavior.

---

## 23. Elasticsearch Analyzer Testing

Before proposing a production reindex for Turkish stemming, provide Kibana
`_analyze` tests for:

```text
telefon telefonlar telefonların telefonlarda telefonlardan telefonu
kitap kitaplar kitapların kitaplarda
kulaklık kulaklıklar kulaklıkların
araba arabalar arabaları
şarj şarjlı şarjın
Apple Samsung iPhone Galaxy S24
B092LWRHRH
```

Evaluate:

- Whether Turkish forms share a useful stem
- Whether brands are damaged
- Whether model codes are damaged
- Whether ASINs are damaged
- Whether special overrides are still needed

Do not state that stemming is correct merely because tokens changed.

Compare search behavior on representative product queries.

---

## 24. Reindex Safety Procedure

Never automatically start a large production reindex.

Before any reindex:

1. Inspect current source index mapping.
2. Inspect current source index settings.
3. Confirm source document count.
4. Create a separate test index.
5. Test analyzer behavior with `_analyze`.
6. Reindex a small sample.
7. Compare sample searches.
8. Estimate storage and duration.
9. Provide the exact production command.
10. Wait for explicit approval.

For a production reindex, use safe practices where appropriate:

- New destination index
- Asynchronous task
- Throttling
- Task monitoring
- Conflict monitoring
- Retry monitoring
- Document count comparison
- Refresh interval restoration
- Explicit refresh
- Alias-based cutover where possible

Never delete the source index automatically after reindexing.

---

## 25. Deployment Workflow

The Streamlit Community Cloud deployment is connected to the GitHub repository.

After local changes are complete:

1. Run syntax checks.
2. Run tests.
3. Run a local application check.
4. Review `git diff`.
5. Confirm no secret was added.
6. Commit when requested.
7. Push when requested.

Pushing to the configured branch should update the existing Streamlit
application.

The existing Streamlit URL normally remains the same.

Do not create a new Streamlit deployment unless the existing deployment cannot
be updated or the user explicitly requests a separate deployment.

Configuration files required at runtime must be included in Git:

```text
config/search_config.json
config/intent_rules.json
config/query_translations.json
config.py
```

Do not commit:

```text
.streamlit/secrets.toml
.env
API keys
virtual environments
cache directories
```

---

## 26. Code Quality

Prefer:

- Small focused functions
- Clear function names
- Type hints
- Dataclasses for structured data
- Centralized validation
- Pure functions for translation and query construction
- Mocked external calls
- Clear fallback behavior
- Minimal dependencies
- Standard library tools where practical

Avoid:

- Giant functions
- Duplicate query-building logic
- Duplicate config access logic
- Unnecessary frameworks
- Hidden side effects
- Misleading comments
- Dead code
- Unbounded query expansion
- Unnecessary Elasticsearch requests
- Bare `except`
- Silent failure of critical systems

Maintain backward compatibility where reasonable.

---

## 27. Task Execution Format

For each task:

### Step 1 — Inspect

Inspect relevant files and understand current behavior.

### Step 2 — Plan

Create a brief internal implementation plan.

Do not stop after presenting the plan unless the user explicitly requested only a
plan.

### Step 3 — Implement

Modify the actual project files.

### Step 4 — Test

Run relevant syntax checks and tests.

### Step 5 — Fix

Resolve failures caused by the change.

### Step 6 — Report

Provide a concise final report containing:

- Summary
- Files changed
- Tests run
- Test results
- Manual steps
- Reindex requirement
- Deployment requirement

---

## 28. Completion Criteria

A requested code task is not complete until:

- Relevant files were inspected
- Actual project files were changed
- Config validation was updated when needed
- Tests were added or updated when needed
- Syntax checks were executed
- Unit tests were executed
- Failures caused by changes were fixed
- Documentation was updated when needed
- Secrets remained protected
- Reindex needs were identified
- Deployment steps were identified

Do not finish with only recommendations when direct implementation was requested.

---

## 29. Final Response Style

Keep the final report concise and factual.

Use this format:

```text
Implemented:
- ...

Files changed:
- ...

Validation:
- python -m py_compile app.py: passed/failed
- python -m py_compile config.py: passed/failed
- python -m pytest -q: passed/failed

Manual Elasticsearch step:
- None / explanation

Deployment step:
- None / git push required

Notes:
- ...
```

Do not claim success for commands that were not run.

Do not expose secrets in the report.
