"""Cross-repository test-result collector for the portfolio.

Created: 2026-08-16
Last updated: 2026-11-10

This package contains the collector: ``config`` reads the source
configuration, ``github`` reads the Actions API, ``parsers`` read the three
per-test formats, ``models`` holds the shared records, ``store`` appends them
to the NDJSON record, ``ingest`` drives that pipeline, ``index`` rebuilds the
SQLite index, and ``reports`` and ``render`` publish the Markdown report and
the HTML page. This file itself defines only ``__version__``.

Ingestion is the only stage that touches the network; it writes ``data/``.
The index is rebuilt from ``data/`` alone, and the reports read only the index,
so every published figure can be reproduced offline from a clone.

Specified by ``DESIGN.md`` sections 1 and 5.
"""

__version__ = "0.1.0"
