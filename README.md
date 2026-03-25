# ASCII–Unicode Diacritics Analyzer Tool

This utility implements Unicode normalization (NFD) to analyze Latin script code points from ICANN’s Label Generation Rules (Latin RZ‑LGR, 2022‑05‑26 XML). It lists characters that canonically decompose to an ASCII base letter (a–z/A–Z) plus combining diacritical mark(s) (Unicode General Category M). Results are grouped by diacritic count and exported to a structured PDF with technical details. Processing uses an in‑memory SQLite database and leaves no temporary files behind.

- Author/Maintainer: Mark W. Datysgeld (mark@governanceprimer.com)
- License: The Unlicense (Public Domain). See LICENSE.txt.

## Dependencies

- Python 3.x
- Packages:
  - reportlab
  - requests

## Install

Using pip (recommended in a virtual environment):

```
pip install -r requirements.txt
```

Or install packages explicitly:

```
pip install reportlab requests
```

## Usage

From the Analyzer-Tool/ directory:

```
py LD-PDP-ASCII-Unicode-Diacritics-Analyzer-Tool.py
```

Optional thesis flags can be passed to append additional sections to the PDF report:

```
py LD-PDP-ASCII-Unicode-Diacritics-Analyzer-Tool.py -thesis-small
```

This will:
- Fetch the Latin RZ‑LGR XML (Unicode 11.0 repertoire).
- Compute the in-scope set (ASCII base + combining diacritics).
- Generate a PDF named: `LD-PDP-ASCII-Unicode-Diacritics-Report-YYYY-MM-DD.pdf`.

### Thesis flags

- `-thesis-small`: appends a section containing only the **additional** Latin repertoire characters whose Unicode name matches `LATIN SMALL LETTER [A-Z] WITH ...`, excluding characters already covered by the default decomposable theory, excluding blocked variants in the RZ-LGR, and excluding non-ASCII name stems such as `EZH`.

## Output

The PDF includes:
- Characters with one diacritic.
- Characters with two diacritics.
- Other sequences in the LGR whose base is ASCII, if any.
- Any optional thesis sections requested by CLI flags.
- Coverage summary and a compact appendix.
- The tool attempts to download Noto Sans (Regular/Bold) for broad Unicode coverage. If unavailable, it falls back to Arial.

## Troubleshooting

- If HTTP fails due to a proxy or firewall, download the appropriate XML locally and adjust `XML_URL` in the script.
