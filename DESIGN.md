# Portfolio Test-Results Collector - Design

> **Document status:** describes the design as of **2026-08-16**, before any
> implementation. Nothing here has been built yet. Decisions are recorded with
> the evidence that produced them, so a later reader can tell which choices were
> forced by measurement and which were judgement calls that may be revisited.

---

## 1. What this is

A standalone, read-only collector that pulls test results out of the GitHub
Actions artifacts of the five portfolio repositories, normalizes them into one
durable store, and publishes descriptive reliability reports over the combined
history.

It is a **downstream consumer**. It depends on the five suites; none of them may
ever depend on it.

### Non-goals

- **It is not a gate.** No pipeline in any source repository blocks on it. A
  reporting tool that can fail someone else's build has been mis-scoped.
- **It does not write to the source repositories.** No commits, no dispatches,
  no issue creation. Read-only against the Actions API.
- **It does not require changes to the test suites.** Everything in v1 is parsed
  from artifacts that already exist. See §4.

---

## 2. The problem, with numbers

Measured 2026-08-16 against the live Actions API:

| Repository | Runs (all time) | Failed runs |
|---|---:|---:|
| apolskiy.github.io | 80 | 0 (Pages/dispatch only) |
| PlaywrightAPWebsiteAutomation | 73 | 15 |
| CountryWeather | 44 | 12 |
| VM-Deployment-and-Configuration | 24 | 4 |
| PublicAP | 16 | 0 |
| **Total** | **237** | **31** |

Three facts follow, and they shape everything below.

**There is no reliability history today.** Every question worth asking - which
test fails most often, is a failure a flake or a regression, is any test getting
slower - requires per-test history across runs. That history exists only inside
per-run artifacts that nobody has ever read together.

**Flakiness is not currently measurable at all.** The canonical definition is
*same input, differing outcome*. No suite configures reruns, so every test is
observed exactly once per run, and a flake is indistinguishable from a
regression at the moment it happens. The one exception is PublicAP, which runs a
four-leg matrix (`ubuntu`/`windows` x `py3.12`/`py3.14`) and therefore produces
four observations of the same commit. Cross-leg disagreement is the only
same-commit reliability signal available anywhere in the portfolio today.

**The history is expiring.** Nothing in any workflow sets `retention-days`, so
every repository is on GitHub's 90-day default - and for public repositories 90
days is the *maximum*, not just the default. There is no setting that prevents
this.

| Repository | Oldest artifact created | Expires |
|---|---|---|
| CountryWeather | 2026-05-21 | **2026-08-19** |
| PlaywrightAPWebsiteAutomation | (within window) | 2026-08-28 |
| PublicAP | (within window) | 2026-10-29 |
| VM-Deployment-and-Configuration | 2026-08-07 | 2026-11-05 |

A collector that persists outside Actions is the only retention mechanism
available. This also settles a design question that would otherwise be open: the
tool must be a **scheduled persister** that pulls and writes to durable storage,
not an on-demand analyzer that reads artifacts live. An on-demand analyzer would
silently return a shorter history every week.

**This puts a date on the first deliverable.** CountryWeather's earliest runs
are gone after 2026-08-19. Backfill comes before reporting, before CI, before
polish. See §12.

---

## 3. Why a separate repository

Recorded because the alternative - a script inside each suite - is the obvious
first instinct.

1. **Coupling only works in one direction.** Embedding the collector inside a
   suite means that suite's pipeline can fail because an analytics dependency
   broke. That makes a reporting tool a gate on a quality gate.
2. **Five copies drift.** This portfolio has already produced this defect twice:
   `code-style.md` shipped as a byte-for-byte copy of `testing-standards.md`,
   and `live.html` sat published as a stale copy of `index.html`. A per-repo
   analysis script is the same failure with a schema attached, and worse -
   divergence surfaces as five reports disagreeing about the same number rather
   than as an obviously duplicated file.
3. **The value proposition is cross-repo by definition.** Per-repo scripts
   cannot normalize identity across suites or share a store, and reconciling
   five outputs afterwards is this project reached by a more expensive route.
4. **It sits on nobody's critical path.** It needs no checkout of any suite and
   spends none of the REST Countries quota.
5. **Heterogeneity is configuration, not code.** See §8.

---

## 4. What the artifacts actually contain

Measured by downloading one artifact from each repository. This is the single
most important input to the design, and it invalidated the initial assumption
that everything emits JUnit XML.

| Repository | Artifact name | n | Total | Format |
|---|---|---:|---:|---|
| PlaywrightAPWebsiteAutomation | `execution-reports` | 57 | 78.8 MB | Allure **report** |
| PlaywrightAPWebsiteAutomation | `allure-results-chromium` | 6 | 1.3 MB | Allure **raw** |
| PlaywrightAPWebsiteAutomation | `pytest-report-chromium` | 6 | 0.6 MB | HTML only |
| PlaywrightAPWebsiteAutomation | `outbound-link-report` | 4 | 0.03 MB | not test results |
| CountryWeather | `qa-artifacts` | 41 | 40.4 MB | Allure report **+ JUnit XML** |
| VM-Deployment-and-Configuration | `allure-results` | 21 | 3.9 MB | Allure **raw** |
| PublicAP | `emulator-reports-{os}-py{ver}` | 13 x 4 | 1.4 MB | **JUnit XML** |

**Total backfill download: ~126 MB.** One-time, trivial.

### The finding that drives the parser design

**The largest suite emits no JUnit XML.** `PlaywrightAPWebsiteAutomation` ships
a generated Allure *report* and a pytest HTML report, and nothing else
machine-readable per test. A JUnit-only collector would cover CountryWeather,
PublicAP and nothing else - missing the two biggest suites in the portfolio.

Adding `--junitxml` to those suites is a one-line change each, but it does not
help retroactively, and retroactive coverage is the entire point given §2. **For
history already frozen in expiring artifacts, only the formats already present
are usable.**

So: **three parsers**, chosen by evidence rather than preference.

### 4.1 Allure raw results (`*-result.json`)

Used by VM-Deployment-and-Configuration (`allure-results/`, at the zip root) and
by PlaywrightAPWebsiteAutomation's six `allure-results-chromium` artifacts.
Confirmed keys from a VM sample:

```
uuid, historyId, testCaseId, fullName, name, status, start, stop,
labels[], titlePath, description   (+ steps[], parameters[] when present)
```

Measured coverage in one VM artifact: 174 `*-result.json`, of which **13 carry
`steps` and 47 carry `parameters`**. `allure.step` appears 25 times across that
suite's tests, so step coverage is real but partial - the schema must not assume
every result has steps.

The 224 `*-container.json` files alongside them hold fixture before/after
stages. Out of scope for v1; noted so a later reader knows they were seen and
skipped deliberately rather than missed.

### 4.2 Allure generated report (`data/test-cases/*.json`)

Used by PlaywrightAPWebsiteAutomation (`reports/allure-report/`) and
CountryWeather (`allure-report/`). A **different schema from 4.1**, not merely a
different path:

```
uid, name, fullName, historyId, status, time{start,stop,duration},
testStage{status, steps[], description}, labels[], parameters[],
statusMessage, flaky, retriesCount, retry, links
```

Three differences from raw results that the parser must reconcile:

| | Raw (4.1) | Report (4.2) |
|---|---|---|
| Timing | `start` / `stop` epoch ms | `time{start, stop, duration}` |
| Steps | top-level `steps[]` | nested in `testStage.steps[]` |
| `name` | may be an `@allure.title` | usually `test_x[param]` |

Both carry `labels[]` with `epic`, `feature`, `story`, `severity`, which gives
reporting a ready-made grouping dimension at no cost.

Worth stating plainly, because it retires a requirement that looked large:
**unified step-level logging does not require editing any test in any
repository.** Both suites already use `allure.step`, and the data is already
structured. It requires a parser.

### 4.3 JUnit XML

Standard pytest output, confirmed shape:

```xml
<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="108"
           time="29.706" timestamp="2026-08-12T22:30:08+00:00" hostname="...">
  <testcase classname="tests.test_countries.TestCountries"
            name="test_country_by_name[germany]" time="0.434" />
```

The only per-test format PublicAP emits, and a second source for CountryWeather.
Carries no steps - an asymmetry the schema represents honestly rather than
papers over. See §6.

---

## 5. Architecture

```text
GitHub Actions REST API
   |  list runs -> list artifacts -> download zip
   v
[ ingest ]  extract only the files a parser needs
   |
   +-- allure_raw parser    (*-result.json)              -> results + steps
   +-- allure_report parser (data/test-cases/*.json)     -> results + steps
   +-- junit parser         (*.xml)                      -> results only
   |
   v
[ durable record ]  data/*.ndjson   (append-only, committed)
   |
   v
[ derived index ]   build/results.sqlite  (gitignored, rebuilt on demand)
   |
   v
[ reports ]  flip rate, duration drift, cross-leg disagreement, never-failed
```

### Storage: append-only text is the record, SQLite is derived

The obvious choice is to commit a SQLite file. It is the wrong one. A binary
rewritten daily stores a full new copy in git history on every commit, and it is
not reviewable in a diff.

Instead the durable record is **append-only NDJSON**, partitioned by repository
and month (`data/countryweather/2026-08.ndjson`). Git stores small deltas
because appends are appends, the content is human-inspectable, and a bad
ingestion is visible in review rather than opaque.

SQLite is then a **derived index**, rebuilt from the NDJSON by `make db` and
gitignored. This keeps the project consistent with a lesson already recorded in
this portfolio - CountryWeather still carries two tracked files under
`test_results/` that were build output committed before the ignore rule existed.
Generated things do not go in git.

---

## 6. Data model

Three record types, written as NDJSON and mirrored into SQLite tables.

### `runs`

| Field | Source | Notes |
|---|---|---|
| `repo` | config | `apolskiy/CountryWeather` |
| `run_id`, `attempt` | API | primary identity |
| `workflow`, `event` | API | `push`, `repository_dispatch`, `schedule`, `workflow_dispatch` |
| `head_sha` | API | test-code version - **not** the system under test, see §7 |
| `status`, `conclusion` | API | |
| `started_at`, `updated_at` | API | UTC |
| `sut_version` | derived | see §7; nullable, and honestly null when unknown |

### `test_results`

| Field | Source | Notes |
|---|---|---|
| `repo`, `run_id`, `attempt` | join key to `runs` | |
| `test_uid` | derived | stable cross-run identity, see §7 |
| `module`, `test_name` | parser | |
| `params` | parser | JSON object: `browser_name`, `env`, matrix legs |
| `status` | parser | normalized: `passed`/`failed`/`broken`/`skipped`/`not_run` |
| `duration_ms` | parser | computed from `stop - start` for raw Allure |
| `message` | parser | failure text, nullable |
| `labels` | Allure only | epic/feature/story/severity, null for JUnit |
| `source_format` | ingest | `allure_raw`/`allure_report`/`junit` - makes every gap below explainable |
| `has_steps` | derived | false for JUnit **and** for Allure results with none |

### `steps`

Allure only. One row per step, with `ordinal`, `name`, `status`, `duration_ms`,
and a nullable `parent_ordinal` for nesting.

`has_steps` is what a report must check before claiming step coverage.
Otherwise "no failing steps recorded" reads as a clean result when it actually
means the source format had none to give - and given §4.1, that is the common
case rather than the edge case: only 13 of 174 sampled VM results carry steps.

### Absent tests are recorded, not omitted

A test that does not appear in a run's artifacts is recorded as **`not_run`**
rather than left missing. `skipped` is reserved for tests the source format
explicitly reported as skipped (JUnit's `<skipped>`, Allure's `skipped` status);
absence is a different fact and gets a different value.

This matters because absence is the normal outcome of the suites' own design.
`pytest -m "not external"` *deselects* rather than skips, `--env=weather` runs a
subset, and a PublicAP matrix leg that never started contributes no rows at all.
Without synthesized rows, "this test has never failed" is indistinguishable from
"this test has not run since May", and a flip-rate denominator silently counts
only the runs where the test happened to appear.

**Synthesis is bounded by the test's observed lifetime.** A `not_run` row is
emitted for a run only when that run falls between the test's first and last
observation for its repository, inclusive. Outside that window the test either
did not exist yet or has been deleted, and marking those runs `not_run` would
manufacture history rather than record it.

**The rows are derived, not observed, so they live in the index rather than the
log.** The NDJSON record holds what the artifacts actually said; `not_run` is an
inference from absence, and inferences belong in the layer that can be rebuilt.
Synthesis therefore happens during `make db` (§5), carries
`source_format: "derived"`, and can be recomputed with a better rule without
rewriting an append-only history.

---

## 7. Identity

The hardest part of this design, and the part most likely to produce confidently
wrong numbers if it is got wrong.

### Test identity

```
test_uid = "{repo}::{module}::{test_name}"
```

taken from `fullName` (`tests.test_config.TestValidationFailures#test_duplicate_vm_names`)
or JUnit's `classname` + `name`, with parameters **stripped from the name and
stored separately** in `params`. So `test_country_by_name[germany]` and
`test_country_by_name[japan]` share a `test_uid` and differ by `params`, which
makes "is this test reliable" answerable at both granularities.

**`name` must never be used as identity.** In VM-Deployment-and-Configuration
the sampled result carries `name: "Duplicate VM names are rejected"` against
`fullName: tests.test_config.TestValidationFailures#test_duplicate_vm_names` -
the suite uses `@allure.title`, so `name` is a human-readable title. Keying on
it would produce identities that silently change whenever someone improves a
title, and history would fork without any error. `fullName` is the key; `name`
is stored as a display label only.

Allure's `historyId` is retained as a secondary column but is not the key: it is
per-repo, opaque, and folds parameters in.

### Assigned test IDs are the preferred key

A derived `test_uid` still forks history when a test is renamed, because the
name is the identity. The fix is an **assigned, stable ID** carried by the test
itself - a per-repository letter prefix plus a never-reused number, validated
against `^[A-Z]{3,4}_\d{5,8}$`:

| Repository | Prefix | Example |
|---|---|---|
| PlaywrightAPWebsiteAutomation | `PAWA` | `PAWA_10001` |
| CountryWeather | `CWA` | `CWA_10001` |
| PublicAP | `PAP` | `PAP_10001` |
| VM-Deployment-and-Configuration | `VMD` | `VMD_10001` |

The test name then becomes a **display label** and may change freely; reports
group and sort by `test_id`. Numbers start at 10001 per repository and are never
reused, so deleting a test retires its number rather than freeing it.

Three constraints follow, and the third is the one that shapes v1.

**It requires editing the suites.** This is the one change that breaks §1's "does
not require changes to the test suites", and it should be made knowingly rather
than discovered later. Roughly 400 test cases carry IDs once every repository is
annotated.

**Each format carries it differently.** Allure has a native mechanism -
`@allure.label("test_id", "PAWA_10001")` - which lands in the `labels[]` array
the parser already reads, for both the raw and report schemas. JUnit XML has no
label concept, so PublicAP needs pytest's `record_property("test_id", ...)`,
which emits `<property name="test_id" value="PAP_10001"/>` inside `<testcase>`.
The parsers therefore read `test_id` from two different places, and neither is
the test name.

**It cannot work retroactively, and that decides the schema.** The artifacts
being backfilled were produced before any ID existed - CountryWeather's oldest
expire in three days and can never be regenerated. So `test_id` is a **nullable
column**, not the primary key:

- Reports key on `COALESCE(test_id, test_uid)`.
- `test_uid` is always populated and remains the join that stitches
  pre-ID history to post-ID history.
- A `test_id` to `test_uid` mapping accumulates as annotated runs arrive, giving
  continuity across the boundary without rewriting the backfilled record.

**Uniqueness must be enforced, or the scheme silently does the opposite of its
job.** A duplicated ID merges two tests' histories into one, which is harder to
notice than a fork because the row count still looks reasonable. The collector
therefore fails ingestion when one `test_id` resolves to more than one
`test_uid` within a repository, rather than reporting an average over two
different tests.

### Run identity, and the trap in it

**`head_sha` does not identify the input for the site suite.** In
PlaywrightAPWebsiteAutomation the event distribution is 32 `push`, **31
`repository_dispatch`**, 2 `schedule`, 8 `workflow_dispatch`. For every dispatch
run the automation repository's SHA is frozen while the actual system under test
- the deployed site - changed underneath.

Commit `3da922d` demonstrates it: five runs, one `push` and four
`repository_dispatch`, one of which failed.

A flakiness detector keyed on `head_sha` would look at that and report a flaky
test. It is far more likely a real site change or a real environment failure.
**That is worse than having no detector**, because it produces a confident wrong
answer instead of an absent one.

Mitigation, in order of preference:

1. Record `sut_version` for the site suite by correlating each run's
   `started_at` against the `apolskiy.github.io` Pages deployment history, which
   the API exposes. Retroactively resolvable for the existing 31 dispatch runs.
2. Where it cannot be resolved, **leave it null and exclude those runs from
   same-input comparisons** rather than falling back to `head_sha`.

Any flakiness query must group by `(test_uid, params, sut_version)` and skip
rows where `sut_version` is null. This is the single constraint most worth
enforcing in code rather than documenting and hoping.

---

## 8. Source configuration

Heterogeneity lives in `config/sources.yaml`, not in code. Three parsers, five
repositories, one config entry each:

```yaml
sources:
  - repo: apolskiy/PlaywrightAPWebsiteAutomation
    artifacts:
      - name: execution-reports
        parser: allure_report
        glob: "reports/allure-report/data/test-cases/*.json"
      - name: allure-results-chromium
        parser: allure_raw
        glob: "*-result.json"

  - repo: apolskiy/CountryWeather
    artifacts:
      - name: qa-artifacts
        parser: allure_report
        glob: "allure-report/data/test-cases/*.json"

  - repo: apolskiy/VM-Deployment-and-Configuration
    artifacts:
      - name: allure-results
        parser: allure_raw
        glob: "*-result.json"

  - repo: apolskiy/PublicAP
    artifacts:
      # Named groups become params columns, so the matrix legs are data.
      - name: "emulator-reports-(?P<os>[\\w-]+)-py(?P<python>[\\d.]+)"
        parser: junit
        glob: "emulator-results.xml"
```

Adding a sixth project is a config entry. That is the concrete form of the claim
in §3 that customization is configuration rather than per-repo code.

Note that VM-Deployment-and-Configuration has run under **two workflow names** -
`CI` (21 runs) and `VM Cluster CI` (3) - so ingestion keys on artifact name and
repository, never on workflow name.

---

## 9. Ingestion mechanics

- **Watermark resume.** Per repository, the highest ingested `run_id` is stored;
  a normal run fetches only what is newer. `--backfill` ignores the watermark.
- **Idempotent.** Upsert keyed on `(repo, run_id, attempt, test_uid,
  params_hash)`. Re-running never duplicates, so a partial failure is fixed by
  running again.
- **Extract selectively.** The zips total ~126 MB mostly because of Allure's
  HTML assets. Only files matching the configured glob are read out of the
  archive; nothing else is written to disk.
- **Rate limits.** Authenticated REST is 5,000 requests/hour; a full backfill is
  roughly 237 run queries plus ~170 artifact downloads. Not close to the limit,
  but the client honours `Retry-After` and backs off on 403/429 rather than
  assuming it never will be.
- **Expired artifacts are normal, not an error.** `expired: true` is skipped
  with a counted warning. After 2026-08-19 this will be the steady state for the
  oldest CountryWeather runs, and the tool must not treat routine data loss as a
  failure.

### Credentials

A **fine-grained PAT, read-only**: resource owner `apolskiy`, repository access
limited to the four source repositories, permission **Actions: read** and
nothing else. Stored as the `PORTFOLIO_READ_TOKEN` secret on this repository
alone - the scope spans four repositories, the secret lives in one. The built-in
`GITHUB_TOKEN` is scoped to the repository running the workflow and cannot read
another's artifacts.

The token is never printed, never written to a file, and never embedded in a
remote URL. Downloading artifacts requires authentication even for public
repositories, which is the only reason a token is needed at all - so it gets the
narrowest scope that works.

---

## 10. Reports in v1

All descriptive, all plain SQL over the derived index.

- **Flip rate** per `(test_uid, params)`: outcome changes over ordered runs,
  restricted to rows with a resolved `sut_version`.
- **Cross-leg disagreement** for PublicAP: same commit, four matrix legs,
  differing outcomes. The only genuine same-input signal available today, and
  the first thing likely to say something real.
- **Duration drift**: p50/p95 per test over time, flagging tests whose p95 has
  moved beyond a threshold. Useful well before failure data accumulates, because
  every passing run contributes a measurement.
- **Never-failed inventory**: tests with no failure in recorded history -
  candidates for a scheduled tier rather than a per-push one.
- **Step-level failure frequency** (Allure sources only): which `allure.step`
  most often precedes a failure. Must respect `has_steps`.

---

## 11. Repository conventions and the lint gate

Matching the rest of the portfolio, because the consistency argument this
project makes about the other repositories applies to itself.

**Pylint at a blocking 10.00/10**, exactly as CountryWeather and PublicAP now
enforce it:

- `pylint==4.0.6` pinned in `requirements.txt`, in the single requirements file
  rather than a separate dev set - the linter must import every third-party name
  the project imports in order to resolve them.
- `make lint` runs `pylint --fail-under=10 $(git ls-files '*.py')`. The file list
  comes from git rather than the recipe, so a new module is covered the moment
  it is tracked.
- A CI `lint` job runs first and every other job declares `needs: lint`.
  `--fail-under=10` rather than a softer floor: a score permitted to drift is not
  a gate, because it never fails a build, it just gets quietly worse.
- **The `.pylintrc` is verified to parse** as part of setting it up, not
  assumed. CountryWeather's sat in place for months with a stray `Ini, TOML`
  first line that left it unparseable, so pylint silently fell back to defaults
  and reported `F0011` where nothing was watching. A config file that is never
  confirmed to load is indistinguishable from one that does nothing. The check
  is cheap: run `pylint --rcfile=.pylintrc` once and confirm no `F0011`.

The rest:

- `Makefile` as the canonical invocation - `make ingest`, `make db`,
  `make report`, `make lint`, `make test`. CI runs the same commands, so a local
  run and a pipeline run are the same command rather than two things that
  resemble each other.
- `readme.md` describing the current release with per-section version stamps;
  `CHANGELOG.md` for release-to-release history; dates UTC.
- Unit tests against **recorded fixture artifacts** committed under
  `tests/fixtures/` rather than against the live API. A parser test that needs
  the network is not a unit test, and would break the day an artifact expires.
  One fixture per format from §4, so all three parsers are covered offline.
- Scheduled daily via `schedule` plus `workflow_dispatch`. Concurrency
  serialized, since two ingestions writing the same NDJSON partition would
  interleave.

---

## 12. Delivery order

Driven by the 2026-08-19 expiry, not by what is most interesting to build.

1. **Backfill first.** Minimum viable path: API client, all three parsers,
   NDJSON writer. Run it manually against all five repositories and persist the
   result. Everything else can be rebuilt later; the data cannot.
2. Derived SQLite index + `make db`.
3. Reports (§10), starting with cross-leg disagreement and duration drift, which
   have data today.
4. CI, lint gate, fixtures, unit tests, readme/CHANGELOG.
5. `sut_version` correlation for the site suite (§7). Deliberately not first:
   it is the highest-value correctness work, but the dispatch history it depends
   on is not expiring as fast as CountryWeather's artifacts.

---

## 13. Risks and open questions

- **Allure's report format is a report format, not a documented API.** The
  `data/test-cases/*.json` shape is stable in practice but carries no
  compatibility promise, and the raw and report schemas already differ in three
  ways (§4.2). Mitigated by fixtures: a format change breaks a unit test rather
  than a nightly job, and already-ingested NDJSON is unaffected.
- **`sut_version` may not be fully recoverable** for older dispatch runs if
  Pages deployment history is shorter than the run history. Where it is not, the
  design choice is to record null rather than guess.
- **Step coverage is thin and uneven.** PublicAP has none (JUnit), and only 13
  of 174 sampled VM results carry steps. Any step-level report covers a minority
  of the corpus, and must say so rather than presenting a percentage computed
  over the subset as though it described the whole.
- **The `not_run` lifetime window is a heuristic** (§6). A test that is genuinely
  dormant for a long stretch and then revived will have that stretch recorded as
  `not_run`, which is correct; but a test renamed rather than deleted appears as
  one identity ending and another beginning, and the window rule cannot tell
  that from a deletion plus an addition. Renames are therefore under-counted
  rather than mis-counted, and the derived layer can be rebuilt if a better rule
  is found.
