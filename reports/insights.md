# Portfolio Test Insights

## Inventory

| Repository | Runs | Tests | Observations | Failures | With trace |
|---|---:|---:|---:|---:|---:|
| CountryWeather | 40 | 17 | 1519 | 12 | 12 |
| PlaywrightAPWebsiteAutomation | 62 | 41 | 3355 | 19 | 19 |
| PublicAP | 13 | 36 | 5136 | 0 | 0 |
| VM-Deployment-and-Configuration | 21 | 140 | 3248 | 0 | 0 |

## Same-input disagreement

None. Every test that ran more than once within a single workflow run - which today means PublicAP's four-leg matrix, the only place the same commit is exercised more than once - agreed with itself every time.

This is the strongest statement the data currently supports, and it is a narrow one. No suite reruns a failure, so a test that fails once and passes on retry would never be observed doing so. Absence of disagreement here is not evidence that no test is flaky; it is evidence that none disagreed across operating system and Python version.

## Failures

**31 failing observations** across 18 distinct test/status pairs. Listed in full rather than charted: a few dozen events is a list, and plotting it would suggest a trend the data cannot support.

| Repository | Test | Status | Count | With trace |
|---|---|---|---:|---:|
| CountryWeather | test_country_present_in_region | broken | 6 | 6 |
| PlaywrightAPWebsiteAutomation | test_case_study_publishes_the_actual_suite_size | failed | 3 | 3 |
| PlaywrightAPWebsiteAutomation | test_landing_page_publishes_the_actual_suite_size | failed | 3 | 3 |
| PlaywrightAPWebsiteAutomation | test_profile_header_and_footer_persist_across_tabs | broken | 2 | 2 |
| PlaywrightAPWebsiteAutomation | test_desktop_layout_has_no_horizontal_overflow | broken | 2 | 2 |
| PlaywrightAPWebsiteAutomation | test_desktop_navigation_renders_on_a_single_row | failed | 2 | 2 |
| PlaywrightAPWebsiteAutomation | test_mobile_navigation_wraps_onto_multiple_rows | failed | 2 | 2 |
| CountryWeather | test_all_population_check | broken | 1 | 1 |
| CountryWeather | test_country_by_name | failed | 1 | 1 |
| CountryWeather | test_nonexistent_country_returns_empty | broken | 1 | 1 |
| CountryWeather | test_nonexistent_country_returns_404 | failed | 1 | 1 |
| CountryWeather | test_nonexistent_country_returns_empty | broken | 1 | 1 |
| CountryWeather | test_forecast_by_city | broken | 1 | 1 |
| PlaywrightAPWebsiteAutomation | test_home_panel_renders_the_skills_matrix | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | test_documentation_link_opens_in_a_hardened_new_tab | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | test_project_panel_publishes_a_documentation_link | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | test_desktop_skills_matrix_shows_column_headers | failed | 1 | 1 |
| PlaywrightAPWebsiteAutomation | test_mobile_skills_matrix_hides_column_headers | failed | 1 | 1 |

## Outcome volatility

How often a test's outcome changed from one run to the next. This is a signal, not a verdict: a test that broke and was fixed flips exactly as much as a test that is genuinely unstable, and only the same-input section above can tell them apart.

| Repository | Test | Flips | Observations |
|---|---|---:|---:|
| PlaywrightAPWebsiteAutomation | test_landing_page_publishes_the_actual_suite_size | 6 | 27 |
| PlaywrightAPWebsiteAutomation | test_case_study_publishes_the_actual_suite_size | 6 | 27 |
| CountryWeather | test_country_present_in_region | 4 | 145 |
| PlaywrightAPWebsiteAutomation | test_mobile_navigation_wraps_onto_multiple_rows | 4 | 62 |
| PlaywrightAPWebsiteAutomation | test_desktop_navigation_renders_on_a_single_row | 4 | 62 |
| CountryWeather | test_forecast_by_city | 2 | 245 |
| CountryWeather | test_country_by_name | 2 | 245 |
| PlaywrightAPWebsiteAutomation | test_project_panel_publishes_a_documentation_link | 2 | 191 |
| PlaywrightAPWebsiteAutomation | test_documentation_link_opens_in_a_hardened_new_tab | 2 | 191 |
| PlaywrightAPWebsiteAutomation | test_mobile_skills_matrix_hides_column_headers | 2 | 62 |

## Duration

Passing runs only, for tests with at least 8 of them - a failed test's duration measures where it gave up, not what it costs. Ranked by how far the 95th percentile sits above the median, which finds tests that are usually fast and occasionally are not.

| Repository | Test | Median | p95 | p95/median | Runs |
|---|---|---:|---:|---:|---:|
| PlaywrightAPWebsiteAutomation | test_route_loads_without_console_or_network_errors | 72 ms | 269 ms | 3.8x | 108 |
| PublicAP | test_uninterpretable_delay_yields_the_sentinel | 1 ms | 3 ms | 3.0x | 100 |
| PublicAP | test_aborted_requests_render_their_own_status | 1 ms | 3 ms | 3.0x | 52 |
| PlaywrightAPWebsiteAutomation | test_route_responds_with_http_200 | 114 ms | 314 ms | 2.7x | 108 |
| VM-Deployment-and-Configuration | test_volume_label | 21 ms | 49 ms | 2.3x | 18 |
| PlaywrightAPWebsiteAutomation | test_case_study_publishes_the_actual_suite_size | 88 ms | 204 ms | 2.3x | 24 |
| PublicAP | test_unsupported_code_returns_404 | 1 ms | 2 ms | 2.0x | 364 |
| PublicAP | test_unroutable_path_returns_404 | 1 ms | 2 ms | 2.0x | 208 |
| VM-Deployment-and-Configuration | test_sensitive_to_each_file | 1 ms | 2 ms | 2.0x | 126 |
| VM-Deployment-and-Configuration | test_stable | 1 ms | 2 ms | 2.0x | 21 |

## What this record cannot tell you

| Repository | Observations | With steps | With assigned ID | Formats |
|---|---:|---:|---:|---|
| CountryWeather | 1519 | 1519 | 0 | allure_report |
| PlaywrightAPWebsiteAutomation | 3355 | 3355 | 0 | allure_raw,allure_report |
| PublicAP | 5136 | 0 | 0 | junit |
| VM-Deployment-and-Configuration | 3248 | 273 | 0 | allure_raw |

Assigned IDs are zero everywhere because every row here predates them. The scheme is live in all four suites now, so rows gathered from the next run onward will carry one; these never can, which is why reports key on `COALESCE(test_id, test_uid)`.

Step coverage is uneven by format, not by choice: JUnit cannot express steps at all, and Allure records them only where a suite used `allure.step`. A step-level statistic computed over the whole corpus would silently describe the subset that has them.

**1 absence(s) recorded as `not_run`.** A test missing from a run inside its own observed lifetime - it existed before, it exists after, and that run did not report it. Absences outside that window are births and deaths rather than skipped work, and are deliberately not synthesized.

## Artifacts that yielded nothing

| Repository | Run | Artifact | Reason | Created | Run outcome |
|---|---:|---|---|---|---|
| CountryWeather | 26206359194 | qa-artifacts | no_members_matched | 2026-05-21 | failure |

An expired artifact is routine. An artifact that exists while containing no results is not: the upload step ran, so the job believed it had something to publish. Each row records the pattern that missed and a sample of what the archive actually held, because a wrong glob and an empty report look identical from the outside and want opposite fixes.

