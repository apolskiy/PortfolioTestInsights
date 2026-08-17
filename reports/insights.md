# Portfolio Test Insights

## Repositories

Each code is the prefix of that suite's assigned test IDs, so a code in any table agrees with the IDs printed beside it. The range shows which IDs that suite has actually published so far - a test recorded before the scheme existed carries none, and appears in the tables with `-` in its ID column.

| Code | Repository | Tests | Assigned IDs in use |
|---|---|---:|---|
| CWA | [apolskiy/CountryWeather](https://github.com/apolskiy/CountryWeather) | 17 | CWA_10001 .. CWA_10009 (+8 without one) |
| PAWA | [apolskiy/PlaywrightAPWebsiteAutomation](https://github.com/apolskiy/PlaywrightAPWebsiteAutomation) | 41 | PAWA_10001 .. PAWA_10042 (+1 without one) |
| PAP | [apolskiy/PublicAP](https://github.com/apolskiy/PublicAP) | 36 | PAP_10007 .. PAP_10042 |
| VMD | [apolskiy/VM-Deployment-and-Configuration](https://github.com/apolskiy/VM-Deployment-and-Configuration) | 140 | VMD_10001 .. VMD_10140 |

## Inventory

| Repo | Runs | Tests | Observations | Failures | With trace |
|---|---:|---:|---:|---:|---:|
| CWA | 41 | 17 | 1552 | 15 | 15 |
| PAWA | 64 | 41 | 3511 | 19 | 19 |
| PAP | 14 | 36 | 5568 | 0 | 0 |
| VMD | 22 | 140 | 3422 | 0 | 0 |

## Same-input disagreement

None. Every test that ran more than once within a single workflow run - which today means PublicAP's four-leg matrix, the only place the same commit is exercised more than once - agreed with itself every time.

This is the strongest statement the data currently supports, and it is a narrow one. No suite reruns a failure, so a test that fails once and passes on retry would never be observed doing so. Absence of disagreement here is not evidence that no test is flaky; it is evidence that none disagreed across operating system and Python version.

## Failures

**34 failing observations** across 21 distinct test/status pairs. Listed in full rather than charted: a few dozen events is a list, and plotting it would suggest a trend the data cannot support.

| Repo | Test ID | Test | Status | Count | With trace |
|---|---|---|---|---:|---:|
| CWA | CWA_10006 | test_country_present_in_region | broken | 6 | 6 |
| PAWA | PAWA_10033 | test_landing_page_publishes_the_actual_suite_size | failed | 3 | 3 |
| PAWA | PAWA_10034 | test_case_study_publishes_the_actual_suite_size | failed | 3 | 3 |
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
| PAWA | PAWA_10034 | test_case_study_publishes_the_actual_suite_size | 6 | 29 |
| PAWA | PAWA_10033 | test_landing_page_publishes_the_actual_suite_size | 6 | 29 |
| CWA | CWA_10006 | test_country_present_in_region | 4 | 150 |
| PAWA | PAWA_10038 | test_mobile_navigation_wraps_onto_multiple_rows | 4 | 64 |
| PAWA | PAWA_10035 | test_desktop_navigation_renders_on_a_single_row | 4 | 64 |
| CWA | CWA_10009 | test_forecast_by_city | 2 | 250 |
| CWA | CWA_10001 | test_country_by_name | 2 | 250 |
| PAWA | PAWA_10031 | test_documentation_link_opens_in_a_hardened_new_tab | 2 | 203 |
| PAWA | PAWA_10028 | test_project_panel_publishes_a_documentation_link | 2 | 203 |
| PAWA | PAWA_10039 | test_mobile_skills_matrix_hides_column_headers | 2 | 64 |

## Duration

Passing runs only, for tests with at least 8 of them - a failed test's duration measures where it gave up, not what it costs. Ranked by how far the 95th percentile sits above the median, which finds tests that are usually fast and occasionally are not.

| Repo | Test ID | Test | Median | p95 | p95/median | Runs |
|---|---|---|---:|---:|---:|---:|
| PAWA | PAWA_10002 | test_route_loads_without_console_or_network_errors | 72 ms | 266 ms | 3.7x | 112 |
| PAP | PAP_10031 | test_aborted_requests_render_their_own_status | 1 ms | 3 ms | 3.0x | 56 |
| PAWA | PAWA_10001 | test_route_responds_with_http_200 | 114 ms | 312 ms | 2.7x | 112 |
| PAWA | PAWA_10034 | test_case_study_publishes_the_actual_suite_size | 86 ms | 204 ms | 2.4x | 26 |
| VMD | VMD_10103 | test_volume_label | 21 ms | 49 ms | 2.3x | 19 |
| PAP | PAP_10028 | test_unsupported_code_returns_404 | 1 ms | 2 ms | 2.0x | 392 |
| PAP | PAP_10029 | test_unroutable_path_returns_404 | 1 ms | 2 ms | 2.0x | 224 |
| VMD | VMD_10131 | test_sensitive_to_each_file | 1 ms | 2 ms | 2.0x | 132 |
| PAP | PAP_10036 | test_uninterpretable_delay_yields_the_sentinel | 1 ms | 2 ms | 2.0x | 120 |
| VMD | VMD_10130 | test_stable | 1 ms | 2 ms | 2.0x | 22 |

## What this record cannot tell you

| Repo | Observations | With steps | With assigned ID | Formats |
|---|---:|---:|---:|---|
| CountryWeather | 1552 | 1552 | 33 | allure_report |
| PlaywrightAPWebsiteAutomation | 3511 | 3511 | 156 | allure_raw,allure_report |
| PublicAP | 5568 | 0 | 432 | junit |
| VM-Deployment-and-Configuration | 3422 | 286 | 174 | allure_raw |

Assigned IDs are zero everywhere because every row here predates them. The scheme is live in all four suites now, so rows gathered from the next run onward will carry one; these never can, which is why reports key on `COALESCE(test_id, test_uid)`.

Step coverage is uneven by format, not by choice: JUnit cannot express steps at all, and Allure records them only where a suite used `allure.step`. A step-level statistic computed over the whole corpus would silently describe the subset that has them.

**1 absence(s) recorded as `not_run`.** A test missing from a run inside its own observed lifetime - it existed before, it exists after, and that run did not report it. Absences outside that window are births and deaths rather than skipped work, and are deliberately not synthesized.

## Artifacts that yielded nothing

| Repo | Run | Artifact | Reason | Created | Run outcome |
|---|---:|---|---|---|---|
| CountryWeather | 26206359194 | qa-artifacts | no_members_matched | 2026-05-21 | failure |
| PlaywrightAPWebsiteAutomation | 31999679216 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32000447197 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32000475911 | execution-reports | no_members_matched | 2026-08-17 | failure |
| PlaywrightAPWebsiteAutomation | 32001067305 | execution-reports | no_members_matched | 2026-08-17 | failure |

An expired artifact is routine. An artifact that exists while containing no results is not: the upload step ran, so the job believed it had something to publish. Each row records the pattern that missed and a sample of what the archive actually held, because a wrong glob and an empty report look identical from the outside and want opposite fixes.

