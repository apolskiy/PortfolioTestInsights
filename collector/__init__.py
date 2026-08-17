"""Cross-repository test-result collector for the portfolio.

Reads GitHub Actions artifacts from the portfolio's test suites, normalizes
three different per-test formats onto one schema, and appends the result to a
durable append-only record that outlives GitHub's 90-day artifact retention.

See DESIGN.md for the measurements the design rests on.
"""

__version__ = "0.1.0"
