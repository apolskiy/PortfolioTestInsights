# Changelog

All notable changes to this project are recorded here. `readme.md` always
describes the **current** release and nothing else; this file is where
release-to-release history lives, so the README never accumulates a sediment of
"as of version X" qualifiers.

Versions follow [Semantic Versioning](https://semver.org/) as applied to a data
collector:

- **Major** - a change to the durable record's schema that an existing reader
  cannot handle.
- **Minor** - new capability, a new source, or a new report.
- **Patch** - fixes and documentation corrections that change no output.

Dates are **UTC**, matching git commit dates and CI runners.

---

## v0.2.0 - 2026-08-17

### Added

- **A derived SQLite index** (`make db`), rebuilt from scratch on every run and
  gitignored. Keeping it disposable is what allows the inference it contains -
  the synthesized absence rows below - to be improved later without rewriting an
  append-only record that is supposed to hold only what the artifacts said.

  It materializes `identity` as `COALESCE(test_id, test_uid)` so every report
  groups the same way, and so history gathered before assigned IDs existed
  stitches to history gathered after.

- **Absence recorded as `not_run`.** A test missing from a run it was expected in
  is now a row rather than a silence, so "never failed" can be told from "has not
  run since May". Synthesis is bounded by each test's observed lifetime: a row
  appears only for runs between that test's first and last appearance, because
  marking runs before it existed would manufacture history rather than record it.

  The bound does most of the work. Across 13,458 observations the rule
  synthesized **exactly one** absence - a route test missing from a single run
  inside its own lifetime. The apparent gaps elsewhere are births and deaths:
  CountryWeather's catalogue holds 17 tests of which **9 are alive at the latest
  run**, which independently matches the 9 functions carrying `CWA_10001`
  through `CWA_10009`.

- **Descriptive reports** (`make report`), covering inventory, same-input
  disagreement, a full failure list, outcome volatility, duration percentiles,
  coverage gaps, and artifacts that yielded nothing.

  Two ideas are kept deliberately apart. **Same-input disagreement** - identical
  commit, different outcome - is the only thing that proves flakiness, and today
  exists only in PublicAP's build matrix. **Volatility** counts outcome changes
  over time and cannot distinguish a flaky test from one that broke and was
  fixed, so it is labelled a signal rather than a verdict.

  Failures are listed in full rather than charted. Thirty-one failing
  observations is a list; plotting it would imply a trend the data cannot carry.

- **A generated HTML page** (`make site`) written to `docs/` for GitHub Pages,
  and **`insights.yml`**, a daily workflow that ingests, rebuilds, regenerates,
  and commits only when the record or the report body actually changed - the
  page differs by its timestamp on every run, and committing that would fill the
  history with changes that say nothing.

  The page is generated rather than written by hand because a hand-maintained
  statistics page states figures nobody re-checks, and rots the first time the
  data moves. That is the defect this project exists to notice, so it is not one
  worth committing here.

- **`ci.yml`**, gating on Pylint at a blocking 10.00/10. Deliberately no test
  job yet: the unit tier runs against fixtures that are not committed, and a
  pytest job collecting zero tests would either fail the build or be silenced
  with a flag that makes the gate meaningless.

### Fixed

- **Same-input disagreement was counting different inputs as disagreement.** The
  first implementation grouped by `(repo, run_id, identity)`, which pooled every
  case of a parametrized test - so a run where the Germany case failed and the
  Brazil case passed was reported as a test disagreeing with itself. Five such
  false positives appeared in the first render, all of them parametrized tests.

  The comparison now holds the test case fixed and varies only the environment
  axes a matrix is meant to vary (`os`, `python`, `browser`). With that
  correction the honest answer is **none**: no test has disagreed with itself on
  identical input. This is the failure mode the design named - a confidently
  wrong answer being worse than no answer - arriving in the first report written
  against it.

---

## v0.1.0 - 2026-08-16

First release. Ingestion only, deliberately: the oldest artifacts in the
portfolio expired three days after the first backfill, so the data was captured
first and everything reproducible was left for after.

### Added

- **Three parsers** - raw Allure results, generated Allure report documents, and
  JUnit XML - because the suites genuinely publish three formats. The largest
  emits no JUnit XML at all, so a JUnit-only collector would have missed most of
  the portfolio, and adding `--junitxml` to the suites would have recovered no
  historical run.

- **The first backfill: 13,458 results across 234 tests and 136 runs**, reaching
  back to 2026-05-21 - three days before the oldest of those artifacts expired.

- **Append-only NDJSON as the durable record**, partitioned by repository and
  month, with the queryable index derived and gitignored.

- **Full stack traces** captured for every non-passing result, from whichever of
  three places the source format keeps them.

- **Anomaly records** for artifacts that matched configuration but yielded no
  results, carrying the pattern that missed and a sample of the archive's actual
  contents. The first backfill found one and it was real: a failed CountryWeather
  run that produced an Allure report shell over zero test cases.
