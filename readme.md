Aleksandr Polskiy

# PortfolioTestInsights

A read-only collector that pulls test results out of the GitHub Actions
artifacts of the portfolio's five repositories, normalizes three different
per-test formats onto one schema, and keeps the combined history in a durable
record that outlives GitHub's artifact retention.

It exists because none of the history was being kept beyond 90 days. Every
reliability question worth asking - which test fails most often, is a failure a
flake or a regression, is anything getting slower - needs per-test history
across runs. That history was being produced faithfully by every suite, and it
was being deleted on a rolling 90-day clock, because for public repositories 90
days is GitHub's retention *maximum* and not merely its default. No setting
prevents it; persisting the data elsewhere is the only mechanism there is.

> **Documentation status:** describes **v0.2.0**, reviewed 2026-08-17.
> Each section carries the release and date its content last changed, so a
> reader arriving at a later version can see at a glance which parts moved. This
> file always describes the *current* release; the reasoning behind the design,
> and the measurements it rests on, live in [DESIGN.md](DESIGN.md).

---

## 1. What it collects

<sub>v0.2.0 &middot; 2026-08-17</sub>

| Repository | Artifact | Format |
|---|---|---|
| PlaywrightAPWebsiteAutomation | `execution-reports`, `allure-results-*` | Allure report, Allure raw |
| CountryWeather | `qa-artifacts` | Allure report |
| VM-Deployment-and-Configuration | `allure-results` | Allure raw |
| PublicAP | `emulator-reports-{os}-py{version}` | JUnit XML |

**As of the first backfill: 13,458 result rows across 234 distinct tests and 136
runs, reaching back to 2026-05-21.**

Three formats rather than one because the suites genuinely publish three. The
largest suite emits no JUnit XML at all, so a JUnit-only collector would have
missed most of the portfolio, and adding `--junitxml` to the suites would not
have recovered a single historical run. See [DESIGN.md §4](DESIGN.md).

---

## 2. Setup

<sub>v0.2.0 &middot; 2026-08-17</sub>

### Prerequisites

* Python 3.14
* `pip install -r requirements.txt`

### Credentials

Downloading artifacts requires authentication even for public repositories,
which is the only reason a token is needed.

* **CI**: a fine-grained PAT stored as `PORTFOLIO_READ_TOKEN`, resource owner
  `apolskiy`, scoped to the four source repositories, permissions
  **Actions: read** and **Contents: read**. Read-only by design - the collector
  never writes to a source repository.
* **Local**: the same variable, or nothing at all. With no variable set the
  client falls back to the locally authenticated `gh` CLI, so a developer who
  has already signed in needs no extra setup.

The token is never printed, never written to a file, and never embedded in a
remote URL.

---

## 3. Running it

<sub>v0.2.0 &middot; 2026-08-17</sub>

```bash
make ingest                                  # pull anything new into the record
make ingest-dry                              # parse everything, write nothing
python -m collector.ingest --repo publicap   # one repository
python -m collector.ingest --verbose         # log every artifact considered
```

Ingestion is **idempotent**: rows are keyed on
`(repo, run_id, attempt, test_uid, params_hash)` and an existing row is never
rewritten, so an interrupted run is fixed by running it again. Duplicate
detection is exact rather than watermark-based, because a run interrupted
halfway through a repository leaves some of a run's rows written and some not.

### Make targets

```bash
make db          # rebuild the derived SQLite index from data/
make report      # render reports/insights.md from the index
make site        # render docs/index.html, the published page
make lint        # pylint over every tracked .py file, gated at 10.00/10
make test        # unit tests against recorded fixtures, no network
make clean       # remove caches; never touches data/
```

**Only `make ingest` needs a token or a network.** `db`, `report` and `site`
read nothing but `data/`, which is committed - so a fresh clone reproduces every
published figure offline. That is the practical payoff of committing the record
rather than keeping a database: the evidence travels with the repository.

### Published automatically

`.github/workflows/insights.yml` runs daily: ingest, rebuild, regenerate, and
commit **only if the record or the report body actually changed**. The page is
rewritten on every run and would otherwise differ by its timestamp alone, which
would fill the history with commits that say nothing.

The page is generated, never hand-written, and that is the point rather than a
convenience. A hand-maintained statistics page states figures nobody re-checks,
so it rots the first time the data moves - which is the exact defect this
project exists to notice. A generated page cannot disagree with its source,
because it holds no independent copy of it.

> **Setup:** enable Pages on this repository with source `main` / `/docs`, and
> add the `PORTFOLIO_READ_TOKEN` secret described above.

---

## 4. Where the data goes

<sub>v0.2.0 &middot; 2026-08-17</sub>

```text
data/
└── {repository}/
    ├── {YYYY-MM}.results.ndjson     # one line per test outcome
    ├── {YYYY-MM}.runs.ndjson        # one line per workflow run
    └── {YYYY-MM}.anomalies.ndjson   # artifacts that yielded no results
```

Append-only NDJSON, partitioned by repository and month. A SQLite file would
have been the obvious choice and is the wrong one: a binary rewritten daily
stores a full new copy in git history on every commit and cannot be reviewed in
a diff. Appends produce small deltas and a readable review. The queryable index
is derived from these files and is gitignored - generated things do not go in
git.

---

## 5. Reporting and observability

<sub>v0.2.0 &middot; 2026-08-17</sub>

**Failure diagnostics are captured, not summarized.** Every non-passing result
carries both its message and its full stack trace, read from
`statusDetails.trace` (Allure raw), `statusTrace` (Allure report), or the body
of the `<failure>` element (JUnit). A message says a test failed; only the trace
says where. All 34 failures in the first backfill carry one.

**Step-level narrative is preserved** where the source has it. Both Allure
schemas expose the `allure.step` tree, flattened here into ordered rows with
`parent_ordinal` retaining the nesting. This is worth stating because it retires
a requirement that looked large: unified step-level logging needed no edit to
any test in any repository, only a parser.

**Coverage gaps are represented rather than hidden.** `source_format`,
`has_steps` and `has_trace` sit on every row, so "no failing steps recorded"
can always be told apart from "this format cannot express any". JUnit carries no
steps at all, and only a minority of Allure results carry them, so a step-level
report that did not check this would quietly describe a fraction of the corpus
as though it were the whole.

**An artifact that yields nothing is recorded as data.** An expired artifact is
routine - 90 days is the retention maximum for public repositories, so loss is
the steady state. But an artifact that exists while containing no test results
is the opposite: the upload step worked, so the run believed it had something to
publish, and it did not. Each one is written to `anomalies.ndjson` with the
reason, the glob that missed, and a sample of what the archive actually held,
because a wrong glob and an empty report produce the same symptom and want
opposite fixes.

The first backfill found one, and it was real: CountryWeather run
`26206359194` failed on 2026-05-21 having produced an Allure report with a
`data/` directory, a timeline and a categories file - but no `test-cases/`
directory at all. The report shell was generated over zero results.

---

## 6. Code standards

<sub>v0.2.0 &middot; 2026-08-17</sub>

* **Pylint at a blocking 10.00/10**, matching CountryWeather and PublicAP.
  `make lint` is the same command CI runs, needs no token, and spends nothing.
* **The `.pylintrc` is verified to parse.** CountryWeather's sat in place for
  months with a stray first line that left it unparseable, so pylint fell back
  to defaults where nothing was watching. A config file never confirmed to load
  is indistinguishable from one that does nothing.
* **An empty lint file list is a failure, not a pass.** `make lint` enumerates
  tracked files from git, and git returns nothing until the first `git add` -
  at which point pylint exits 0 having linted nothing. The recipe checks for
  that and fails loudly, because a gate that passes by finding nothing is the
  same defect as an rcfile that never loaded.
* **Strict type hints** on every parameter and return value, `from __future__
  import annotations` throughout.
* **Google-style docstrings** on every module, class and function, stating what
  a thing does, what it assumes, and what it returns - including the cases that
  return `None` and why that is the honest answer rather than a guess.
* **Names are at least three characters and say what they hold**, enforced by
  `variable-rgx` / `argument-rgx` in `.pylintrc` rather than left to review. A
  convention nothing checks is a preference.

---

## 7. Known limits

<sub>v0.2.0 &middot; 2026-08-17</sub>

* **`head_sha` does not identify the input for the site suite.** Half of
  PlaywrightAPWebsiteAutomation's runs are `repository_dispatch`, where the
  automation SHA is frozen while the deployed site changed underneath. A
  flakiness query keyed on it would report real site regressions as flakes, so
  `sut_version` is recorded separately and left null when it cannot be resolved.
  Resolving it retroactively is the next substantial piece of work.
* **Assigned test IDs reach the data from 2026-08-16 onward, never before.** All
  four suites now publish one - `PAWA_*`, `CWA_*`, `PAP_*`, `VMD_*` - as an
  Allure label and a JUnit property, and both parsers read it. But the 13,458
  rows already backfilled predate the scheme and can never carry one, because
  the artifacts they came from are frozen and some have since expired. So
  `test_id` stays nullable and reports key on `COALESCE(test_id, test_uid)`,
  with `test_uid` remaining the join that stitches pre-ID history to post-ID
  history.
* **Same-input evidence is narrow, and the report says so.** The only place the
  same commit runs more than once is PublicAP's four-leg matrix, and no suite
  reruns a failure. So "no test disagreed with itself" is a true statement about
  a small window, not a clean bill of health, and the report prints that caveat
  next to the result rather than leaving a reader to infer it.

* **Volatility is a signal, not a verdict.** A test that broke and was fixed
  flips exactly as often as one that is genuinely unstable. Only the same-input
  section can tell them apart, and today it is nearly empty.
