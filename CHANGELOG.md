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

## v0.3.1 - 2026-08-18

### Fixed

- **The README still described identity as `COALESCE(test_id, test_uid)`** in
  §7, three releases after v0.2.0 replaced it. The behaviour was corrected; the
  documentation of it was not, so the file described the exact defect the code
  had stopped having - and did so in the section a reader consults precisely
  because they want to know how history survives a rename.

  §7 now states the rule that actually runs (an ID observed anywhere for a test
  becomes its identity everywhere), what it deliberately is not, and why the
  backward propagation is only sound alongside the uniqueness check. A stale
  doc is worse here than a missing one: it reads as verified.

  All four suite READMEs repeated the same claim about PortfolioTestInsights in
  their own Test Identity sections and have been corrected alongside this one.

---

## v0.3.0 - 2026-08-17

### Added

- **A duration error budget.** A run breaches when it exceeds a multiple of that
  test's own baseline median; the budget allows a fraction of the trailing
  window to breach before it is spent. Policy lives in `config/sources.yaml`.

  The objective is **relative** by necessity: no suite here declares a per-test
  duration SLA, so an absolute threshold would have to be seeded from this
  history and then measured against it, which proves nothing. It also does not
  restate CountryWeather's 5.0s/3.0s API thresholds - those govern one HTTP
  request while this record holds whole-test durations, and reusing the number
  would create a second gate measuring a different thing under the same name.

  It reports rather than gates, per section 1 of DESIGN.md. A suite that owns an
  SLO is the thing entitled to fail a build over it.

- **A `floor_ms` threshold, added because the first draft was wrong.** The
  relative objective alone reported **51 of 225 tests as having spent their
  budget**. The data says why: the median test in this record has a **1 ms**
  baseline and **145 of 234** sit under 10 ms, so a 3x tolerance means "breached
  at 3 ms" - scheduler jitter and timer granularity, not degradation. Tests
  whose baseline falls under 100 ms are now excluded outright, and the measured
  justification is recorded in the config beside the number. With the floor in
  place, 78 tests carry a verdict and none has breached.

- **Container test results are collected.** PublicAP's `image_tests` run outside
  `testpaths` and appeared in no ingested artifact, so `PAP_10001..PAP_10006`
  were assigned IDs for tests this record had never seen. Both image jobs now
  emit JUnit and upload it, and `image` is captured as a parameter rather than
  split across two config entries - the same tests run against the image built
  from the commit and the image already published, which is a real difference in
  the system under test. It is deliberately **not** an environment axis, so a
  disagreement between the two images is reported rather than pooled away.

- **A repositories legend**, published once ahead of the tables, carrying each
  suite's short code, a link, its test count, and the range of assigned IDs
  actually in use. The range is what exposed the container gap above: PublicAP's
  started at `PAP_10007`.

### Changed

- **Every per-test table now carries the test name beside its ID.** An assigned
  ID is the right key and the wrong label - `PAWA_10020` is stable across
  renames precisely because it carries no meaning, so a report printing only the
  ID sends every reader to the source to find out which test it is. Under a test
  management system the ID would resolve to a title; there is none here, and the
  report should not assume one.

- **Repository columns use the short code** (`CWA`, `PAWA`, `PAP`, `VMD`) rather
  than the full name, which cost a third of every table's width to repeat
  something a reader learns once. The codes are the prefixes of each suite's
  assigned IDs, so the column agrees with the IDs beside it instead of being a
  second naming scheme.

### Fixed

- **The unassigned-ID count reported every test as unassigned.** Identity
  stitching sets a test's identity from any ID observed for it, but the older
  rows still carry `NULL` in `test_id`, so counting NULLs directly claimed
  CountryWeather had "17 tests, +17 without one" beside a range covering nine of
  them. The count now collapses to one row per test first. CWA reads 9 assigned
  plus 8 unassigned - and those 8 are the deleted tests the lifetime-window rule
  identified independently.

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

- **Another run's results were being attributed to the current run.**
  `allure generate` merges a `history/` directory, so a generated report holds
  the current run's results *and* retained entries from earlier runs, the latter
  flagged `retry: true`. The parser read both, which recorded one test as having
  passed *and* failed inside a single run id - with start timestamps six hours
  apart. Found by reconciling the record against the index and noticing three
  failing rows had gone missing: 200 keys held two rows, and the index's primary
  key silently kept one.

  Retained entries are now skipped, and the record was regenerated.
  CountryWeather's observation count fell from 1,719 to 1,552 - the difference
  was another run's history counted twice. Raw Allure results are written by the
  run itself and carry no merged history, so only the report parser needs the
  guard.

- **Assigned IDs split each test's history instead of stitching it.** The index
  keyed on `COALESCE(test_id, test_uid)`, so the moment the suites began
  publishing IDs, every earlier row stayed keyed by uid while every later row
  keyed by ID - and the inventory dutifully counted PublicAP's 36 tests as 72
  and VM's 140 as 280. One long history became two short ones, which is the
  precise failure the IDs were introduced to prevent.

  An ID observed anywhere for a test now becomes that test's identity
  everywhere, including on rows recorded before the ID existed. Uniqueness is
  enforced while building the map: one ID claimed by two tests raises rather
  than quietly averaging over two different tests.

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
