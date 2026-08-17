"""Render the Markdown report as a self-contained HTML page.

The page is **generated from the record on every run**, never hand-written, and
that is the whole point rather than a convenience. A hand-maintained statistics
page states figures nobody re-checks, so it rots silently the first time the
data moves - which is precisely the defect this project exists to notice. A
generated page cannot disagree with its source, because it has no independent
copy of it.

The converter handles only the subset of Markdown the reports emit: headings,
pipe tables, paragraphs, and inline bold and code. Reaching for a Markdown
library would add a dependency to a nightly job in order to support syntax
nothing here produces.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Final

#: Inline styles. The page is served from a different origin than the portfolio
#: site, so it carries its own presentation rather than importing a stylesheet
#: it cannot reach.
STYLES: Final[str] = """
:root {
  --bg: #ffffff; --fg: #1c1c1c; --muted: #5a5a5a; --line: #d8d8d8;
  --head: #f2f2f2; --accent: #0b5fa5; --code: #f4f4f4;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14171a; --fg: #e8e8e8; --muted: #a3a3a3; --line: #2f343a;
    --head: #1d2126; --accent: #6fb3ef; --code: #1d2126;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem;
  background: var(--bg); color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
}
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.9rem; margin: 0 0 .35rem; }
h2 { font-size: 1.25rem; margin: 2.5rem 0 .75rem; padding-bottom: .35rem;
     border-bottom: 1px solid var(--line); }
p { margin: .8rem 0; }
.subtitle { color: var(--muted); margin: 0 0 2rem; font-size: .95rem; }
.table-wrap { overflow-x: auto; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .93rem; }
th, td { padding: .5rem .7rem; border: 1px solid var(--line); text-align: left; }
th { background: var(--head); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
code { background: var(--code); padding: .12rem .35rem; border-radius: 3px;
       font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .88em; }
a { color: var(--accent); }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: .88rem; }
"""

_BOLD: Final[re.Pattern[str]] = re.compile(r"\*\*(.+?)\*\*")
_CODE: Final[re.Pattern[str]] = re.compile(r"`([^`]+)`")
_SEPARATOR: Final[re.Pattern[str]] = re.compile(r"^\|[\s:|-]+\|$")


def _inline(text: str) -> str:
    """Convert inline Markdown to HTML, escaping everything else.

    Args:
        text: One line of Markdown, without block syntax.

    Returns:
        HTML-safe markup with bold and code spans applied.
    """
    escaped = html.escape(text)
    escaped = _CODE.sub(r"<code>\1</code>", escaped)
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


def _split_row(line: str) -> list[str]:
    """Split one pipe-table row into its cells.

    Args:
        line: A Markdown table row.

    Returns:
        Cell contents, with the empty edges produced by leading and trailing
        pipes discarded.
    """
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _render_table(rows: list[str], numeric: list[bool]) -> list[str]:
    """Render a collected pipe table as HTML.

    Args:
        rows: Table lines, header first, separator already removed.
        numeric: One flag per column, True where the separator marked it
            right-aligned.

    Returns:
        HTML lines, wrapped so a wide table scrolls inside itself rather than
        forcing the page to scroll sideways.
    """
    out = ['<div class="table-wrap">', "<table>"]
    header, *body = rows
    out.append("<thead><tr>")
    for index, cell in enumerate(_split_row(header)):
        css = ' class="num"' if index < len(numeric) and numeric[index] else ""
        out.append(f"<th{css}>{_inline(cell)}</th>")
    out.append("</tr></thead><tbody>")
    for line in body:
        out.append("<tr>")
        for index, cell in enumerate(_split_row(line)):
            css = ' class="num"' if index < len(numeric) and numeric[index] else ""
            out.append(f"<td{css}>{_inline(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return out


def markdown_to_html(markdown: str) -> str:
    """Convert the report's Markdown subset to HTML.

    Args:
        markdown: The rendered report.

    Returns:
        HTML for the page body.
    """
    out: list[str] = []
    table: list[str] = []
    numeric: list[bool] = []

    def flush() -> None:
        """Emit any table collected so far."""
        if table:
            out.extend(_render_table(table, numeric))
            table.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if _SEPARATOR.match(stripped):
            numeric = [cell.endswith(":") for cell in _split_row(stripped)]
            continue
        if stripped.startswith("|"):
            table.append(stripped)
            continue
        flush()
        if not stripped:
            continue
        if stripped.startswith("## "):
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        else:
            out.append(f"<p>{_inline(stripped)}</p>")
    flush()
    return "\n".join(out)


def render_page(markdown: str, source_url: str) -> str:
    """Wrap the converted report in a complete HTML document.

    Args:
        markdown: The rendered report.
        source_url: Repository this page was generated from, linked in the
            footer so a reader can check the figures against the record.

    Returns:
        A self-contained HTML document.
    """
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = markdown_to_html(markdown)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Test Insights</title>
<meta name="description" content="Cross-repository test reliability report, generated
 from the durable record on every ingestion.">
<style>{STYLES}</style>
</head>
<body>
<main>
{body}
<footer>
<p>Generated {generated} from the append-only record in
<a href="{html.escape(source_url)}">PortfolioTestInsights</a>. Every figure on this
page is produced by <code>make site</code> from that record - nothing here is written
by hand, so nothing here can drift away from its source.</p>
</footer>
</main>
</body>
</html>
"""
