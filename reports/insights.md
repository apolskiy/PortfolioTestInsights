# Portfolio Test Insights

## Inventory

| Repository | Runs | Tests | Observations | Failures | With trace |
|---|---:|---:|---:|---:|---:|
| CountryWeather | 41 | 17 | 1552 | 15 | 15 |
| PlaywrightAPWebsiteAutomation | 64 | 41 | 3511 | 19 | 19 |
| PublicAP | 14 | 36 | 5568 | 0 | 0 |
| VM-Deployment-and-Configuration | 22 | 140 | 3422 | 0 | 0 |

## Same-input disagreement

None. Every test that ran more than once within a single workflow run - which today means PublicAP's four-leg matrix, the only place the same commit is exercised more than once - agreed with itself every time.

This is the strongest statement the data currently supports, and it is a narrow one. No suite reruns a failure, so a test that fails once and passes on retry would never be observed doing so. Absence of disagreement here is not evidence that no test is flaky; it is evidence that none disagreed across operating system and Python version.

## Failures

**34 failing observations** across 21 distinct test/status pairs. Listed in full rather than charted: a few dozen events is a list, and plotting it would suggest a trend the data cannot support.

| Repository | Test | Status | Count | With trace |
|---|---|---|---:|---:|
| CountryWeather | CWA_10006 | broken | 6 | 6 |
| PlaywrightAPWebsiteAutomation | PAWA_10033 | failed | 3 | 3 |
| PlaywrightAPWebsiteAutomation | PAWA_10034 | failed | 3 | 3 |
| PlaywrightAPWebsiteAutomation | PAWA_10026 | broken | 2 | 2 |
| PlaywrightAPWebsiteAutomation | PAWA_10035 | failed | 2 | 2 |
| PlaywrightAPWebsiteAutomation | PAWA_10037 | broken | 2 | 2 |
| PlaywrightAPWebsiteAutomation | PAWA_10038 | failed | 2 | 2 |
| CountryWeather | CWA_10001 | failed | 1 | 1 |
| CountryWeather | CWA_10005 | broken | 1 | 1 |
| CountryWeather | CWA_10007 | broken | 1 | 1 |
| CountryWeather | CWA_10009 | broken | 1 | 1 |
| CountryWeather | test_all_countries_population_integrity | broken | 1 | 1 |
| CountryWeather | test_germany_cross_reference_region | broken | 1 | 1 |
| CountryWeather | test_germany_schema_validation | broken | 1 | 1 |
| CountryWeather | test_nonexistent_country_returns_404 | failed | 1 | 1 |
| CountryWeather | test_nonexistent_country_returns_empty | broken | 1 | 1 |
| PlaywrightAPWebsiteAutomation | PAWA_10027 | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | PAWA_10028 | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | PAWA_10031 | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | PAWA_10036 | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | PAWA_10039 | failed | 1 | 1 |

## Outcome volatility

How often a test's outcome changed from one run to the next. This is a signal, not a verdict: a test that broke and was fixed flips exactly as much as a test that is genuinely unstable, and only the same-input section above can tell them apart.

| Repository | Test | Flips | Observations |
|---|---|---:|---:|
| PlaywrightAPWebsiteAutomation | PAWA_10034 | 6 | 29 |
| PlaywrightAPWebsiteAutomation | PAWA_10033 | 6 | 29 |
| CountryWeather | CWA_10006 | 4 | 150 |
| PlaywrightAPWebsiteAutomation | PAWA_10038 | 4 | 64 |
| PlaywrightAPWebsiteAutomation | PAWA_10035 | 4 | 64 |
| CountryWeather | CWA_10009 | 2 | 250 |
| CountryWeather | CWA_10001 | 2 | 250 |
| PlaywrightAPWebsiteAutomation | PAWA_10031 | 2 | 203 |
| PlaywrightAPWebsiteAutomation | PAWA_10028 | 2 | 203 |
| PlaywrightAPWebsiteAutomation | PAWA_10039 | 2 | 64 |

## Duration

Passing runs only, for tests with at least 8 of them - a failed test's duration measures where it gave up, not what it costs. Ranked by how far the 95th percentile sits above the median, which finds tests that are usually fast and occasionally are not.

| Repository | Test | Median | p95 | p95/median | Runs |
|---|---|---:|---:|---:|---:|
| PlaywrightAPWebsiteAutomation | PAWA_10002 | 72 ms | 266 ms | 3.7x | 112 |
| PublicAP | PAP_10031 | 1 ms | 3 ms | 3.0x | 56 |
| PlaywrightAPWebsiteAutomation | PAWA_10001 | 114 ms | 312 ms | 2.7x | 112 |
| PlaywrightAPWebsiteAutomation | PAWA_10034 | 86 ms | 204 ms | 2.4x | 26 |
| VM-Deployment-and-Configuration | VMD_10103 | 21 ms | 49 ms | 2.3x | 19 |
| PublicAP | PAP_10028 | 1 ms | 2 ms | 2.0x | 392 |
| PublicAP | PAP_10029 | 1 ms | 2 ms | 2.0x | 224 |
| VM-Deployment-and-Configuration | VMD_10131 | 1 ms | 2 ms | 2.0x | 132 |
| PublicAP | PAP_10036 | 1 ms | 2 ms | 2.0x | 120 |
| VM-Deployment-and-Configuration | VMD_10130 | 1 ms | 2 ms | 2.0x | 22 |

## What this record cannot tell you

| Repository | Observations | With steps | With assigned ID | Formats |
|---|---:|---:|---:|---|
| CountryWeather | 1552 | 1552 | 33 | allure_report |
| PlaywrightAPWebsiteAutomation | 3511 | 3511 | 156 | allure_raw,allure_report |
| PublicAP | 5568 | 0 | 432 | junit |
| VM-Deployment-and-Configuration | 3422 | 286 | 174 | allure_raw |

Assigned IDs are zero everywhere because every row here predates them. The scheme is live in all four suites now, so rows gathered from the next run onward will carry one; these never can, which is why reports key on `COALESCE(test_id, test_uid)`.

Step coverage is uneven by format, not by choice: JUnit cannot express steps at all, and Allure records them only where a suite used `allure.step`. A step-level statistic computed over the whole corpus would silently describe the subset that has them.

**1 absence(s) recorded as `not_run`.** A test missing from a run inside its own observed lifetime - it existed before, it exists after, and that run did not report it. Absences outside that window are births and deaths rather than skipped work, and are deliberately not synthesized.

## Artifacts that yielded nothing

| Repository | Run | Artifact | Reason | Created | Run outcome |
|---|---:|---|---|---|---|
| CountryWeather | 26206359194 | qa-artifacts | no_members_matched | 2026-05-21 | failure |
| PlaywrightAPWebsiteAutomation | 31999679216 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32000447197 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32000475911 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32001067305 | execution-reports | no_members_matched | 2026-08-17 | failure |

An expired artifact is routine. An artifact that exists while containing no results is not: the upload step ran, so the job believed it had something to publish. Each row records the pattern that missed and a sample of what the archive actually held, because a wrong glob and an empty report look identical from the outside and want opposite fixes.

