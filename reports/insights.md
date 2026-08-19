# Portfolio Test Insights

## Repositories

Each code is the prefix of that suite's assigned test IDs, so a code in any table agrees with the IDs printed beside it. The range shows which IDs that suite has actually published so far - a test recorded before the scheme existed carries none, and appears in the tables with `-` in its ID column.

| Code | Repository | Tests | Assigned IDs in use |
|---|---|---:|---|
| CWA | [apolskiy/CountryWeather](https://github.com/apolskiy/CountryWeather) | 17 | CWA_10001 .. CWA_10009 (+8 without one) |
| PAWA | [apolskiy/PlaywrightAPWebsiteAutomation](https://github.com/apolskiy/PlaywrightAPWebsiteAutomation) | 41 | PAWA_10001 .. PAWA_10042 (+1 without one) |
| PAP | [apolskiy/PublicAP](https://github.com/apolskiy/PublicAP) | 42 | PAP_10001 .. PAP_10042 |
| VMD | [apolskiy/VM-Deployment-and-Configuration](https://github.com/apolskiy/VM-Deployment-and-Configuration) | 140 | VMD_10001 .. VMD_10140 |

## Inventory

| Repo | Runs | Tests | Observations | Failures | With trace |
|---|---:|---:|---:|---:|---:|
| CWA | 41 | 17 | 1552 | 15 | 15 |
| PAWA | 68 | 41 | 3847 | 21 | 21 |
| PAP | 16 | 42 | 6028 | 0 | 0 |
| VMD | 23 | 140 | 3596 | 0 | 0 |

## Same-input disagreement

None. Every test that ran more than once within a single workflow run - which today means PublicAP's four-leg matrix, the only place the same commit is exercised more than once - agreed with itself every time.

This is the strongest statement the data currently supports, and it is a narrow one. No suite reruns a failure, so a test that fails once and passes on retry would never be observed doing so. Absence of disagreement here is not evidence that no test is flaky; it is evidence that none disagreed across operating system and Python version.

## Failures

**36 failing observations** across 21 distinct test/status pairs. Listed in full rather than charted: a few dozen events is a list, and plotting it would suggest a trend the data cannot support.

| Repo | Test ID | Test | Status | Count | With trace |
|---|---|---|---|---:|---:|
| CWA | CWA_10006 | test_country_present_in_region | broken | 6 | 6 |
| PAWA | PAWA_10033 | test_landing_page_publishes_the_actual_suite_size | failed | 4 | 4 |
| PAWA | PAWA_10034 | test_case_study_publishes_the_actual_suite_size | failed | 4 | 4 |
| PAWA | PAWA_10026 | test_profile_header_and_footer_persist_across_tabs | broken | 2 | 2 |
| PAWA | PAWA_10035 | test_desktop_navigation_renders_on_a_single_row | failed | 2 | 2 |
| PAWA | PAWA_10037 | test_desktop_layout_has_no_horizontal_overflow | broken | 2 | 2 |
| PAWA | PAWA_10038 | test_mobile_navigation_wraps_onto_multiple_rows | failed | 2 | 2 |
| CWA | CWA_10001 | test_country_by_name | failed | 1 | 1 |
| CWA | CWA_10005 | test_all_population_check | broken | 1 | 1 |
| CWA | CWA_10007 | test_nonexistent_country_returns_empty | broken | 1 | 1 |
| CWA | CWA_10009 | test_forecast_by_city | broken | 1 | 1 |
| CWA | - | test_all_countries_population_integrity | broken | 1 | 1 |
| CWA | - | test_germany_cross_reference_region | broken | 1 | 1 |
| CWA | - | test_germany_schema_validation | broken | 1 | 1 |
| CWA | - | test_nonexistent_country_returns_404 | failed | 1 | 1 |
| CWA | - | test_nonexistent_country_returns_empty | broken | 1 | 1 |
| PAWA | PAWA_10027 | test_home_panel_renders_the_skills_matrix | failed | 1 | 1 |
| PAWA | PAWA_10028 | test_project_panel_publishes_a_documentation_link | failed | 1 | 1 |
| PAWA | PAWA_10031 | test_documentation_link_opens_in_a_hardened_new_tab | failed | 1 | 1 |
| PAWA | PAWA_10036 | test_desktop_skills_matrix_shows_column_headers | failed | 1 | 1 |
| PAWA | PAWA_10039 | test_mobile_skills_matrix_hides_column_headers | failed | 1 | 1 |

## Outcome volatility

How often a test's outcome changed from one run to the next. This is a signal, not a verdict: a test that broke and was fixed flips exactly as much as a test that is genuinely unstable, and only the same-input section above can tell them apart.

| Repo | Test ID | Test | Flips | Observations |
|---|---|---|---:|---:|
| PAWA | PAWA_10034 | test_case_study_publishes_the_actual_suite_size | 8 | 33 |
| PAWA | PAWA_10033 | test_landing_page_publishes_the_actual_suite_size | 8 | 33 |
| CWA | CWA_10006 | test_country_present_in_region | 4 | 150 |
| PAWA | PAWA_10038 | test_mobile_navigation_wraps_onto_multiple_rows | 4 | 68 |
| PAWA | PAWA_10035 | test_desktop_navigation_renders_on_a_single_row | 4 | 68 |
| CWA | CWA_10009 | test_forecast_by_city | 2 | 250 |
| CWA | CWA_10001 | test_country_by_name | 2 | 250 |
| PAWA | PAWA_10031 | test_documentation_link_opens_in_a_hardened_new_tab | 2 | 227 |
| PAWA | PAWA_10028 | test_project_panel_publishes_a_documentation_link | 2 | 227 |
| PAWA | PAWA_10039 | test_mobile_skills_matrix_hides_column_headers | 2 | 68 |

## Duration

Passing runs only, for tests with at least 8 of them - a failed test's duration measures where it gave up, not what it costs. Ranked by how far the 95th percentile sits above the median, which finds tests that are usually fast and occasionally are not.

| Repo | Test ID | Test | Median | p95 | p95/median | Runs |
|---|---|---|---:|---:|---:|---:|
| PAWA | PAWA_10002 | test_route_loads_without_console_or_network_errors | 72 ms | 269 ms | 3.8x | 124 |
| PAP | PAP_10031 | test_aborted_requests_render_their_own_status | 1 ms | 3 ms | 3.0x | 60 |
| PAWA | PAWA_10001 | test_route_responds_with_http_200 | 114 ms | 312 ms | 2.7x | 124 |
| PAWA | PAWA_10034 | test_case_study_publishes_the_actual_suite_size | 86 ms | 204 ms | 2.4x | 29 |
| VMD | VMD_10103 | test_volume_label | 23 ms | 49 ms | 2.1x | 20 |
| PAP | PAP_10028 | test_unsupported_code_returns_404 | 1 ms | 2 ms | 2.0x | 420 |
| PAP | PAP_10029 | test_unroutable_path_returns_404 | 1 ms | 2 ms | 2.0x | 240 |
| PAP | PAP_10036 | test_uninterpretable_delay_yields_the_sentinel | 1 ms | 2 ms | 2.0x | 140 |
| VMD | VMD_10131 | test_sensitive_to_each_file | 1 ms | 2 ms | 2.0x | 138 |
| VMD | VMD_10130 | test_stable | 1 ms | 2 ms | 2.0x | 23 |

## Duration error budget

A run breaches when it exceeds **3x** that test's baseline median. The budget allows **10%** of the trailing **20** runs to breach before it is spent. Tests with fewer than 10 observations in the window, or a baseline median under 100 ms, are omitted - a multiple of a millisecond is measurement noise rather than degradation.

**No test breached its objective in the trailing window.** 78 tests carried enough observations to judge; the rest were omitted by the thresholds above rather than passing quietly.

## What this record cannot tell you

| Repo | Observations | With steps | With assigned ID | Formats |
|---|---:|---:|---:|---|
| CountryWeather | 1552 | 1552 | 33 | allure_report |
| PlaywrightAPWebsiteAutomation | 3847 | 3847 | 492 | allure_raw,allure_report |
| PublicAP | 6028 | 0 | 892 | junit |
| VM-Deployment-and-Configuration | 3596 | 299 | 348 | allure_raw |

The assigned-ID column counts rows carrying one, not tests that have one. All four suites publish IDs, but every row backfilled before 2026-08-16 predates the scheme and can never gain one, since the artifacts are frozen and some have expired. Identity is therefore not `COALESCE(test_id, test_uid)` - that would key earlier rows by uid and later rows by ID, splitting one long history into two short ones at the changeover. An ID observed anywhere for a test is applied to every row for that test instead.

Step coverage is uneven by format, not by choice: JUnit cannot express steps at all, and Allure records them only where a suite used `allure.step`. A step-level statistic computed over the whole corpus would silently describe the subset that has them.

**37 absence(s) recorded as `not_run`.** A test missing from a run inside its own observed lifetime - it existed before, it exists after, and that run did not report it. Absences outside that window are births and deaths rather than skipped work, and are deliberately not synthesized.

## Artifacts that yielded nothing

| Repo | Run | Artifact | Reason | Created | Run outcome |
|---|---:|---|---|---|---|
| CountryWeather | 26200550442 | qa-artifacts | expired | 2026-05-21 | failure |
| CountryWeather | 26200821473 | qa-artifacts | expired | 2026-05-21 | failure |
| CountryWeather | 26200910904 | qa-artifacts | expired | 2026-05-21 | failure |
| CountryWeather | 26206077943 | qa-artifacts | expired | 2026-05-21 | failure |
| CountryWeather | 26206359194 | qa-artifacts | no_members_matched | 2026-05-21 | failure |
| PublicAP | 30842805006 | emulator-reports-ubuntu-latest-py3.12 | download_failed | 2026-08-03 | success |
| PublicAP | 30842805006 | emulator-reports-ubuntu-latest-py3.14 | download_failed | 2026-08-03 | success |
| PublicAP | 30842805006 | emulator-reports-windows-latest-py3.12 | download_failed | 2026-08-03 | success |
| PublicAP | 30842805006 | emulator-reports-windows-latest-py3.14 | download_failed | 2026-08-03 | success |
| PlaywrightAPWebsiteAutomation | 31999679216 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32000447197 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32000475911 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32001067305 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32003827285 | execution-reports | no_members_matched | 2026-08-17 | cancelled |

An expired artifact is routine. An artifact that exists while containing no results is not: the upload step ran, so the job believed it had something to publish. Each row records the pattern that missed and a sample of what the archive actually held, because a wrong glob and an empty report look identical from the outside and want opposite fixes.

