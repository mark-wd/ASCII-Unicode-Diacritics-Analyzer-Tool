#!/usr/bin/env python3
"""
ASCII-Unicode Diacritics Analyzer Tool
On behalf of the ICANN Latin Script Diacritics Policy Development Process WG (LD-WG)

For inquiries about the code, contact:
Mark W. Datysgeld (mark@governanceprimer.com)

This utility implements Unicode normalization (NFD) to analyze Latin script code points from ICANN's Label Generation Rules. It identifies characters that canonically decompose to ASCII base characters plus combining diacritical marks (Unicode General Category M). Results are categorized by diacritic count and output to a structured PDF report with complete Unicode technical data. The implementation uses in-memory SQLite storage and leaves no temporary files behind.
"""

"""
UNLICENSE
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <https://unlicense.org>
"""

import os
import re
import sys
import json
import sqlite3
import unicodedata
import requests
import xml.etree.ElementTree as ET
import tempfile
import urllib.request
import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.frames import Frame
from reportlab.lib.colors import black

# Constants
TOOL_VERSION = "v1.4.0"
XML_URL = "https://www.icann.org/sites/default/files/lgr/rz-lgr-5-latin-script-26may22-en.xml"
# Generate filename with current date in YYYY-MM-DD format
current_date = datetime.date.today().strftime("%Y-%m-%d")
PDF_OUTPUT = f"LD-PDP-ASCII-Unicode-Diacritics-Report-{current_date}.pdf"
WEB_JSON_OUTPUT = os.path.join('web', 'data', 'latest.json')
ASCII_LETTERS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
THESIS_SMALL_NAME_PATTERN = re.compile(r'^LATIN SMALL LETTER [A-Z] WITH .+$')


def format_code_point_string(characters):
    """Return one or more code points in U+XXXX format."""
    return ' '.join(f"U+{ord(c):04X}" for c in characters)


def build_detailed_decomposition(characters):
    """Build a report-friendly decomposition string with names and code points."""
    detailed_decomp_parts = []
    for c in characters:
        char_name = unicodedata.name(c, 'UNKNOWN')
        code_point = f"U+{ord(c):04X}"

        # Add extra spacing around combining characters to prevent them from
        # visually merging with surrounding text in the PDF.
        if unicodedata.category(c).startswith('M'):
            formatted_char = f"&nbsp;{c}&nbsp;"
        else:
            formatted_char = c

        detailed_decomp_parts.append(f"{formatted_char} ({char_name}, {code_point})")

    return ' &nbsp;&nbsp;+&nbsp;&nbsp; '.join(detailed_decomp_parts)


def build_plain_decomposition(characters):
    """Build a plain-text decomposition string for JSON/web output."""
    parts = []
    for c in characters:
        char_name = unicodedata.name(c, 'UNKNOWN')
        code_point = f"U+{ord(c):04X}"
        formatted_char = f" {c} " if unicodedata.category(c).startswith('M') else c
        parts.append(f"{formatted_char} ({char_name}, {code_point})")

    return ' + '.join(parts)


def print_usage():
    """Print CLI usage information."""
    script_name = os.path.basename(__file__)
    print(f"Usage: py {script_name} [-thesis-small] [--json-output PATH] [--json-only] [--web-json]")
    print()
    print("Optional thesis flags:")
    for flag, definition in THESIS_FLAGS.items():
        print(f"  {flag:<16} {definition['help']}")
    print()
    print("Optional output flags:")
    print("  --json-output PATH   Write web-ready JSON output to PATH.")
    print("  --json-only          Skip PDF generation (requires --json-output or --web-json).")
    print(f"  --web-json           Shortcut for --json-output {WEB_JSON_OUTPUT} --json-only.")


def parse_cli_args(argv):
    """Parse supported CLI flags without changing the current default behavior."""
    enabled_flags = []
    seen = set()
    unknown_flags = []
    json_output = None
    json_only = False

    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg in ('-h', '--help'):
            print_usage()
            raise SystemExit(0)

        if arg in THESIS_FLAGS:
            if arg not in seen:
                enabled_flags.append(arg)
                seen.add(arg)
        elif arg == '--json-output':
            if index + 1 >= len(argv):
                raise ValueError("--json-output requires a file path.")
            json_output = argv[index + 1]
            index += 1
        elif arg == '--json-only':
            json_only = True
        elif arg == '--web-json':
            json_output = WEB_JSON_OUTPUT
            json_only = True
        else:
            unknown_flags.append(arg)

        index += 1

    if unknown_flags:
        raise ValueError(
            f"Unknown argument(s): {', '.join(unknown_flags)}. Use -h or --help for usage."
        )

    if json_only and not json_output:
        raise ValueError("--json-only requires --json-output PATH or --web-json.")

    return {
        'enabled_thesis_flags': enabled_flags,
        'json_output': json_output,
        'json_only': json_only,
    }



def setup_temp_database():

    # Set up an in-memory SQLite database for temporary storage. This returns: sqlite3.Connection: Database connection object
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    
    # Create table for storing character data
    cursor.execute('''
    CREATE TABLE characters (
        id INTEGER PRIMARY KEY,
        character TEXT,
        name TEXT,
        decomposition TEXT,
        is_latin INTEGER,
        has_ascii_base INTEGER
    )
    ''')
    
    conn.commit()
    return conn

def store_data_in_db(characters, conn):
    # Store character data in the database with Unicode information. Returns: int: Number of characters stored
    cursor = conn.cursor()
    
    for char in characters:
        name = unicodedata.name(char, '')
        decomposition = unicodedata.decomposition(char)
        
        # Check if it's a Latin script character
        is_latin = 1 if 'LATIN' in name else 0
        
        # Store in database
        cursor.execute(
            'INSERT INTO characters (character, name, decomposition, is_latin, has_ascii_base) VALUES (?, ?, ?, ?, 0)',
            (char, name, decomposition, is_latin)
        )
    
    conn.commit()
    return len(characters)

def analyze_characters(conn):
    """
    Analyze characters to find those with ASCII base + diacritics.   
    Returns:
        tuple: Two lists of tuples:
            - Characters with one diacritic: (character, base_char, diacritic, detailed_decomp)
            - Characters with two or more diacritics: (character, base_char, diacritics, detailed_decomp)
    """
    cursor = conn.cursor()
    
    # Get all Latin characters
    cursor.execute('SELECT id, character, name, decomposition FROM characters WHERE is_latin = 1')
    latin_chars = cursor.fetchall()
    
    # Separate results by number of diacritics
    one_diacritic_results = []
    two_diacritics_results = []
    
    for char_id, char, name, decomposition in latin_chars:
        # Get NFD (decomposed) form
        nfd_form = unicodedata.normalize('NFD', char)
        
        # Check if decomposition has exactly one base character that is ASCII
        base_is_ascii = False
        has_diacritic = False
        
        if len(nfd_form) > 1:  # Has at least one combining character
            base_char = nfd_form[0]
            if base_char in ASCII_LETTERS:
                base_is_ascii = True
                # Count diacritics (combining marks)
                diacritic_count = sum(1 for c in nfd_form[1:] if unicodedata.category(c).startswith('M'))
                has_diacritic = diacritic_count > 0
        
        if base_is_ascii and has_diacritic:
            # Update database
            cursor.execute('UPDATE characters SET has_ascii_base = 1 WHERE id = ?', (char_id,))
            
            # Extract base character and diacritic separately
            base_char = nfd_form[0]
            
            # Get all diacritics (combining marks)
            diacritics = ''.join(c for c in nfd_form[1:] if unicodedata.category(c).startswith('M'))
            detailed_decomp = build_detailed_decomposition(nfd_form)
            
            # Add to appropriate result list based on number of diacritics
            if len(diacritics) == 1:
                one_diacritic_results.append((char, base_char, diacritics, detailed_decomp))
            else:
                two_diacritics_results.append((char, base_char, diacritics, detailed_decomp))
    
    conn.commit()
    return (one_diacritic_results, two_diacritics_results)


# ===== NEW: XML parsing and classification helpers =====
def parse_lgr_xml(url):
    """
    Parse the normative Latin RZ-LGR XML and return:
      - latin_points: list[int] of single code points in the repertoire
      - latin_sequences: list[list[int]] of repertoire sequences
      - blocked_variants: set[str] of blocked variant characters/sequences
    """
    resp = requests.get(url)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    # Find data element (namespace-agnostic; contains <char> items)
    data_elem = None
    for elem in root.iter():
        if elem.tag.endswith('data'):
            data_elem = elem
            break

    latin_points = []
    latin_sequences = []
    blocked_variants = set()
    if data_elem is None:
        return (latin_points, latin_sequences, blocked_variants)

    for ch in data_elem:
        if not ch.tag.endswith('char'):
            continue

        for child in ch:
            if not child.tag.endswith('var'):
                continue
            if child.get('type') != 'blocked':
                continue

            var_attr = child.get('cp') or child.get('cps')
            if not var_attr:
                continue

            codes = [int(part, 16) for part in var_attr.strip().split()]
            blocked_variants.add(''.join(chr(code) for code in codes))

        # Filter non-Latin single code points by tag attribute (e.g., tag="sc:Grek").
        # Sequences often omit 'tag'; we collect them and filter later by ASCII-base logic.
        tag_attr = ch.get('tag')
        if tag_attr and 'sc:Latn' not in tag_attr:
            continue

        # Handle ranges, if any
        first_cp = ch.get('first-cp')
        last_cp = ch.get('last-cp')
        if first_cp and last_cp:
            start = int(first_cp, 16)
            end = int(last_cp, 16)
            for code in range(start, end + 1):
                latin_points.append(code)
            continue

        # Handle single or sequence
        cp_attr = ch.get('cp') or ch.get('cps')
        if not cp_attr:
            continue
        parts = cp_attr.strip().split()
        codes = [int(p, 16) for p in parts]
        if len(codes) == 1:
            latin_points.append(codes[0])
        else:
            latin_sequences.append(codes)

    # Deduplicate while preserving order
    seen = set()
    uniq_points = []
    for cp in latin_points:
        if cp not in seen:
            seen.add(cp)
            uniq_points.append(cp)

    seen_seq = set()
    uniq_sequences = []
    for seq in latin_sequences:
        t = tuple(seq)
        if t not in seen_seq:
            seen_seq.add(t)
            uniq_sequences.append(seq)

    return (uniq_points, uniq_sequences, blocked_variants)


def classify_sequences_ascii_base(latin_sequences):
    """
    From repertoire sequences, keep only those that start with an ASCII base letter
    followed by one or more combining marks in NFD.
    Returns list of tuples:
      (combined_char, base_char, diacritics, detailed_decomp)
    """
    results = []
    for codes in latin_sequences:
        chars = [chr(cp) for cp in codes]
        combined = ''.join(chars)
        nfd = unicodedata.normalize('NFD', combined)
        if not nfd:
            continue
        base = nfd[0]
        if base not in ASCII_LETTERS:
            continue
        diacritics = ''.join(c for c in nfd[1:] if unicodedata.category(c).startswith('M'))
        if not diacritics:
            continue

        # Detailed decomposition built from the original code points in the sequence
        detailed_decomp = build_detailed_decomposition(chars)
        results.append((combined, base, diacritics, detailed_decomp))
    return results


def collect_thesis_small_from_db(conn):
    """
    Collect Latin repertoire characters whose Unicode name matches:
    LATIN SMALL LETTER [A-Z] WITH ...
    """
    cursor = conn.cursor()
    cursor.execute('SELECT character, name FROM characters WHERE is_latin = 1')
    rows = cursor.fetchall()

    results = []
    for char, name in rows:
        if not THESIS_SMALL_NAME_PATTERN.match(name):
            continue

        nfd_form = unicodedata.normalize('NFD', char)
        results.append((
            char,
            format_code_point_string(char),
            name,
            build_detailed_decomposition(nfd_form),
        ))

    return results


def filter_thesis_entries_to_additions(conn, thesis_entries, blocked_variants=None):
    """Keep only entries that are new to the thesis and not blocked in the RZ-LGR."""
    cursor = conn.cursor()
    cursor.execute('SELECT character FROM characters WHERE is_latin = 1 AND has_ascii_base = 1')
    already_in_scope = {row[0] for row in cursor.fetchall()}
    blocked_variants = blocked_variants or set()

    return [
        entry for entry in thesis_entries
        if entry[0] not in already_in_scope and entry[0] not in blocked_variants
    ]


THESIS_FLAGS = {
    '-thesis-small': {
        'title': "Thesis Section: Latin Small Letters",
        'description': (
            "Additional Latin repertoire characters whose Unicode name matches "
            "the pattern 'LATIN SMALL LETTER [A-Z] WITH ...', excluding those "
            "already covered by the default decomposable theory and excluding "
            "blocked variants in the RZ-LGR."
        ),
        'help': "Append only additional, non-blocked characters named 'LATIN SMALL LETTER [A-Z] WITH ...'.",
        'collector': collect_thesis_small_from_db,
    },
}


def collect_requested_thesis_sections(conn, enabled_flags, blocked_variants=None):
    """Build thesis sections requested through CLI flags."""
    thesis_sections = []
    for flag in enabled_flags:
        definition = THESIS_FLAGS[flag]
        raw_entries = definition['collector'](conn)
        filtered_entries = filter_thesis_entries_to_additions(conn, raw_entries, blocked_variants)
        thesis_sections.append({
            'flag': flag,
            'title': definition['title'],
            'description': definition['description'],
            'entries': filtered_entries,
        })
    return thesis_sections


def get_latin_repertoire_rows(conn):
    """Return all Latin repertoire single code points stored in the database."""
    cursor = conn.cursor()
    cursor.execute('SELECT character, name FROM characters WHERE is_latin = 1')
    return cursor.fetchall()


def get_base_in_scope_characters(conn):
    """Return the default decomposable in-scope character set."""
    cursor = conn.cursor()
    cursor.execute('SELECT character FROM characters WHERE is_latin = 1 AND has_ascii_base = 1')
    return {row[0] for row in cursor.fetchall()}


def build_scope_snapshot(conn, thesis_sections=None):
    """Build the effective in-scope set and appendix for a given thesis selection."""
    thesis_sections = thesis_sections or []
    effective_in_scope = set(get_base_in_scope_characters(conn))

    for section in thesis_sections:
        effective_in_scope.update(entry[0] for entry in section.get('entries', []))

    repertoire_rows = get_latin_repertoire_rows(conn)
    out_of_scope_index = []
    for ch, name in repertoire_rows:
        if ch in ASCII_LETTERS:
            continue
        if ch in effective_in_scope:
            continue
        out_of_scope_index.append((ch, f"U+{ord(ch):04X}", name))

    return {
        'in_scope_characters': effective_in_scope,
        'out_of_scope_index': out_of_scope_index,
        'coverage_counts': {
            'total_points': len(repertoire_rows),
            'in_scope': len(effective_in_scope),
            'out_of_scope': len(out_of_scope_index),
        },
    }


def build_table_section(section_id, title, description, columns, rows):
    """Return a generic table section structure for compact web rendering."""
    return {
        'id': section_id,
        'title': title,
        'description': description,
        'type': 'table',
        'rowCount': len(rows),
        'columns': columns,
        'rows': rows,
    }


def serialize_analysis_rows(results):
    """Serialize standard analysis rows for JSON/web output."""
    rows = []
    for char, base_char, diacritics, _detailed_decomp in results:
        decomposition_source = list(char) if len(char) > 1 else unicodedata.normalize('NFD', char)
        rows.append({
            'character': char,
            'codePoints': format_code_point_string(char),
            'base': base_char,
            'diacritics': diacritics,
            'details': build_plain_decomposition(decomposition_source),
        })
    return rows


def serialize_thesis_rows(entries):
    """Serialize thesis rows for JSON/web output."""
    rows = []
    for char, code_point, name, _detailed_decomp in entries:
        rows.append({
            'character': char,
            'codePoint': code_point,
            'name': name,
            'details': build_plain_decomposition(unicodedata.normalize('NFD', char)),
        })
    return rows


def serialize_out_of_scope_rows(out_of_scope_index):
    """Serialize appendix rows for JSON/web output."""
    return [
        {
            'character': ch,
            'codePoint': cp,
            'name': name,
        }
        for ch, cp, name in out_of_scope_index
    ]


def build_primary_web_sections(results_tuple, sequences_ascii_base):
    """Build the common compact sections shown in every web mode except the appendix."""
    one_diacritic_results, two_diacritics_results = results_tuple

    return [
        build_table_section(
            'one-diacritic',
            'One Diacritic',
            'Latin repertoire characters whose canonical decomposition is an ASCII base letter plus one combining mark.',
            [
                {'key': 'character', 'label': 'Char'},
                {'key': 'codePoints', 'label': 'Code point'},
                {'key': 'base', 'label': 'Base'},
                {'key': 'diacritics', 'label': 'Mark'},
                {'key': 'details', 'label': 'Technical details'},
            ],
            serialize_analysis_rows(one_diacritic_results),
        ),
        build_table_section(
            'two-plus-diacritics',
            'Two or More Diacritics',
            'Latin repertoire characters whose canonical decomposition is an ASCII base letter plus multiple combining marks.',
            [
                {'key': 'character', 'label': 'Char'},
                {'key': 'codePoints', 'label': 'Code point'},
                {'key': 'base', 'label': 'Base'},
                {'key': 'diacritics', 'label': 'Marks'},
                {'key': 'details', 'label': 'Technical details'},
            ],
            serialize_analysis_rows(two_diacritics_results),
        ),
        build_table_section(
            'lgr-sequences',
            'Other LGR Sequences',
            'Repertoire sequences in the LGR that begin with an ASCII base letter and continue with combining marks.',
            [
                {'key': 'character', 'label': 'Sequence'},
                {'key': 'codePoints', 'label': 'Code points'},
                {'key': 'base', 'label': 'Base'},
                {'key': 'diacritics', 'label': 'Marks'},
                {'key': 'details', 'label': 'Technical details'},
            ],
            serialize_analysis_rows(sequences_ascii_base),
        ),
    ]


def build_appendix_web_section(out_of_scope_index):
    """Build the appendix section for a specific effective scope."""
    return build_table_section(
        'out-of-scope',
        'Appendix: Out of Scope',
        'Latin repertoire entries that are not canonically decomposable to an ASCII base plus combining marks.',
        [
            {'key': 'character', 'label': 'Glyph'},
            {'key': 'codePoint', 'label': 'Code point'},
            {'key': 'name', 'label': 'Name'},
        ],
        serialize_out_of_scope_rows(out_of_scope_index),
    )

def build_web_mode(mode_id, label, description, enabled_flags, primary_sections, thesis_sections, scope_snapshot, total_sequences, ascii_base_sequences):
    """Build one web mode, combining common sections with any thesis sections."""
    serialized_thesis_sections = []
    thesis_counts = {}
    for section in thesis_sections:
        rows = serialize_thesis_rows(section.get('entries', []))
        serialized_thesis_sections.append(build_table_section(
            section['flag'].lstrip('-'),
            section['title'],
            section.get('description', ''),
            [
                {'key': 'character', 'label': 'Char'},
                {'key': 'codePoint', 'label': 'Code point'},
                {'key': 'name', 'label': 'Name'},
                {'key': 'details', 'label': 'Technical details'},
            ],
            rows,
        ))
        thesis_counts[section['flag']] = len(rows)

    return {
        'id': mode_id,
        'label': label,
        'description': description,
        'enabledFlags': enabled_flags,
        'coverageSummary': {
            **scope_snapshot.get('coverage_counts', {}),
            'total_sequences': total_sequences,
            'ascii_base_sequences': ascii_base_sequences,
            'thesisCounts': thesis_counts,
        },
        'sections': [
            *serialized_thesis_sections,
            *primary_sections,
            build_appendix_web_section(scope_snapshot.get('out_of_scope_index', [])),
        ],
    }


def build_web_report_payload(conn, results_tuple, sequences_ascii_base, latin_sequences, thesis_sections_by_flag):
    """Build the full JSON payload consumed by the compact web frontend."""
    primary_sections = build_primary_web_sections(results_tuple, sequences_ascii_base)
    base_plus_small_sections = []
    if '-thesis-small' in thesis_sections_by_flag:
        base_plus_small_sections.append(thesis_sections_by_flag['-thesis-small'])

    base_scope_snapshot = build_scope_snapshot(conn, [])
    base_plus_small_scope_snapshot = build_scope_snapshot(conn, base_plus_small_sections)

    return {
        'generatedAt': datetime.datetime.now().isoformat(),
        'toolVersion': TOOL_VERSION,
        'source': {
            'xmlUrl': XML_URL,
            'reportDate': current_date,
        },
        'defaultMode': 'base',
        'modes': [
            build_web_mode(
                'base',
                'Base thesis',
                'Core decomposable theory only.',
                [],
                primary_sections,
                [],
                base_scope_snapshot,
                len(latin_sequences),
                len(sequences_ascii_base),
            ),
            build_web_mode(
                'base-plus-small',
                'Base + Latin Small Letters',
                'Core decomposable theory plus the additional Latin Small Letters thesis section.',
                ['-thesis-small'],
                primary_sections,
                base_plus_small_sections,
                base_plus_small_scope_snapshot,
                len(latin_sequences),
                len(sequences_ascii_base),
            ),
        ],
    }


def write_web_json_report(output_path, payload):
    """Write the compact web payload to disk."""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    return output_path


def setup_fonts():
    """
    Set up fonts for PDF generation. Downloads Noto Sans for optimal Unicode support, with Arial as a reliable fallback for all platforms.
    Returns:
        tuple: (main_font_name, bold_font_name) to use in the PDF
    """
    # Noto Sans as the primary choice for Unicode support
    font_url = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf"
    bold_font_url = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf"
    
    temp_dir = tempfile.gettempdir()
    font_path = os.path.join(temp_dir, "NotoSans-Regular.ttf")
    bold_font_path = os.path.join(temp_dir, "NotoSans-Bold.ttf")
    
    # Default to Arial if Noto Sans fails
    main_font = 'Arial'
    bold_font = 'Arial-Bold'
    
    try:
        # Download and register Noto Sans fonts
        if not os.path.exists(font_path):
            print("Downloading Noto Sans font for optimal Unicode support...")
            urllib.request.urlretrieve(font_url, font_path)
            
        if not os.path.exists(bold_font_path):
            urllib.request.urlretrieve(bold_font_url, bold_font_path)
            
        # Register the fonts with ReportLab
        pdfmetrics.registerFont(TTFont('NotoSans', font_path))
        pdfmetrics.registerFont(TTFont('NotoSans-Bold', bold_font_path))
        
        main_font = 'NotoSans'
        bold_font = 'NotoSans-Bold'
        print("Using Noto Sans fonts for optimal Unicode character rendering")
    except Exception as e:
        print(f"Note: Using Arial fonts (Noto Sans unavailable: {e})")
        
    return (main_font, bold_font)

class PDFDocTemplate(BaseDocTemplate):
    """A custom document template that supports hyperlinks."""
    def __init__(self, filename, **kw):
        BaseDocTemplate.__init__(self, filename, **kw)
        self.allowSplitting = 1  # Allow tables to split across pages
        
        # Create a single frame for the content
        frame = Frame(
            self.leftMargin, 
            self.bottomMargin, 
            self.width, 
            self.height, 
            id='normal'
        )
        
        # Add the frame to the page template
        template = PageTemplate(id='normal', frames=[frame])
        self.addPageTemplates([template])

def generate_pdf_report(results_tuple, sequences_ascii_base, out_of_scope_index, coverage_summary, output_filename, thesis_sections=None):
    """
    Generate a PDF report with the analysis results.
    Args:
        results_tuple (tuple): Tuple containing two lists of character data
        output_filename (str): Name of the output PDF file
    """
    one_diacritic_results, two_diacritics_results = results_tuple
    thesis_sections = thesis_sections or []
    
    # Process sequences with ASCII base from LGR (XML-derived)
    other_lgr_occurrences = sequences_ascii_base
    
    print(f"Generating PDF report to {output_filename}...")
    print(f"Found {len(one_diacritic_results)} characters with one diacritic")
    print(f"Found {len(two_diacritics_results)} characters with two diacritics")
    
    # Set up fonts for the PDF
    main_font, bold_font = setup_fonts()
    
    # Create PDF document with hyperlink support
    doc = PDFDocTemplate(output_filename, pagesize=letter)
    styles = getSampleStyleSheet()
    
    # Create custom styles with hyperlink support
    custom_style = ParagraphStyle(
        'CustomStyle',
        parent=styles['Normal'],
        fontName=main_font,
        fontSize=10,
        leading=14,
        linkUnderline=0,  # Disable underline for links
        textColor=black,
    )
    
    # Larger font for simple decomposition
    simple_decomp_style = ParagraphStyle(
        'SimpleDecompStyle',
        parent=styles['Normal'],
        fontName=main_font,
        fontSize=14,
        leading=18,
    )
    
    # Smaller font for detailed decomposition
    detailed_decomp_style = ParagraphStyle(
        'DetailedDecompStyle',
        parent=styles['Normal'],
        fontName=main_font,
        fontSize=8,
        leading=10,
    )
    
    # Create heading styles
    heading2_style = ParagraphStyle(
        'Heading2',
        parent=styles['Heading2'],
        fontName=bold_font,
        fontSize=14,
        leading=18,
        spaceAfter=10,
    )

    # Build content
    content = []
    
    # Main Title
    title_style = styles['Heading1']
    content.append(Paragraph("ASCII-Unicode Diacritics Analysis Report", title_style))
    content.append(Paragraph("On behalf of the ICANN Latin Script Diacritics Policy Development Process WG (LD-WG)", heading2_style))

   # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content.append(Paragraph(f"This version of the report was generated at: {timestamp}", styles['Italic']))

    # Introduction
    explanation = f"This report was generated using the ASCII-Unicode Diacritics Analyzer Tool ({TOOL_VERSION}), and can be generated by any other interested party with <a href='https://github.com/mark-wd/ASCII-Unicode-Diacritics-Analyzer-Tool/tree/main' color='blue'>the tool's Python source code in Github</a>, released under 'The Unlicense', equivalent to Public Domain. This software was developed independently by a community member, with no official affiliation with or endorsement by the ICANN organization.<br/><br/>The tool implements Unicode normalization (NFD) to analyze Latin script code points from ICANN's <a href='https://www.icann.org/sites/default/files/lgr/rz-lgr-5-latin-script-26may22-en.html' color='blue'>Label Generation Rules</a> and identifies characters that canonically decompose to ASCII base characters plus combining diacritical marks (Unicode General Category M). Results are categorized by diacritic count and output to this structured PDF report with complete Unicode technical data.<br/><br/>For inquiries about the code, contact the maintainer:<br/>Mark W. Datysgeld (mark@governanceprimer.com)"
    
    content.append(Spacer(1, 20))
    content.append(Paragraph(explanation, custom_style))

    # Optional thesis sections appear before the main decomposition tables
    if thesis_sections:
        content.append(Spacer(1, 30))

    for thesis_section in thesis_sections:
        entries = thesis_section.get('entries', [])
        content.append(Paragraph(f"{thesis_section['title']} ({len(entries)})", heading2_style))
        if thesis_section.get('description'):
            content.append(Paragraph(thesis_section['description'], custom_style))
            content.append(Spacer(1, 12))

        if entries:
            thesis_table_data = [["Character", "Code point", "Name", "Technical Details"]]

            for char, code_point, name, detailed_decomp in entries:
                char_cell = Paragraph(
                    f"<para align='center'><font face='{main_font}' size='16'>{char}</font></para>",
                    custom_style,
                )
                code_point_cell = Paragraph(code_point, detailed_decomp_style)
                name_cell = Paragraph(name, detailed_decomp_style)
                detailed_decomp_cell = Paragraph(
                    f"<font face='{main_font}'>{detailed_decomp}</font>",
                    detailed_decomp_style,
                )
                thesis_table_data.append([
                    char_cell,
                    code_point_cell,
                    name_cell,
                    detailed_decomp_cell,
                ])

            thesis_table = Table(thesis_table_data, colWidths=[60, 80, 190, 200])
            thesis_table_style = TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#32CCCC')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), bold_font),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
            ])

            for i in range(1, len(thesis_table_data)):
                if i % 2 == 0:
                    thesis_table_style.add('BACKGROUND', (0, i), (-1, i), colors.lightgrey)

            thesis_table.setStyle(thesis_table_style)
            content.append(thesis_table)
        else:
            content.append(Paragraph("No characters matched this thesis.", custom_style))

        content.append(Spacer(1, 30))
    
    # ===== TABLE 1: Characters with One Diacritic =====
    content.append(Paragraph(f"Characters with One Diacritic Mark ({len(one_diacritic_results)})", heading2_style))
    
    # Create table data for one diacritic
    table1_data = [["Character", "Base", "Diacritic", "Technical Details"]]  # Header row
    
    # Process each result into paragraphs
    for char, base_char, diacritic, detailed_decomp in one_diacritic_results:
        # For the character column, show both the character and its code point
        code_point = f"U+{ord(char):04X}"
        char_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='16'>{char}</font><br/><font size='8'>{code_point}</font></para>", custom_style)
        
        base_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='14'>{base_char}</font></para>", simple_decomp_style)

        diacritic_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='14'>{diacritic}</font></para>", simple_decomp_style)

        detailed_decomp_cell = Paragraph(f"<font face='{main_font}'>{detailed_decomp}</font>", detailed_decomp_style)
        
        table1_data.append([char_cell, base_cell, diacritic_cell, detailed_decomp_cell])
    
    # Create table with four columns
    table1 = Table(table1_data, colWidths=[80, 70, 70, 310])
    
    # Style the table
    table1_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#32CCCC')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),  # Center align header text
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),   # Left align content text
        ('FONTNAME', (0, 0), (-1, 0), bold_font),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
    ])
    
    # Add alternating row colors
    for i in range(1, len(table1_data)):
        if i % 2 == 0:
            table1_style.add('BACKGROUND', (0, i), (-1, i), colors.lightgrey)
    
    table1.setStyle(table1_style)
    content.append(table1)
    
    # Add space between tables
    content.append(Spacer(1, 30))
    
    # ===== TABLE 2: Characters with Two Diacritics =====
    if two_diacritics_results:
        content.append(Paragraph(f"Characters with Two Diacritic Marks ({len(two_diacritics_results)})", heading2_style))
        
        # Create table data for two diacritics
        table2_data = [["Character", "Base", "Diacritics", "Technical Details"]]  # Header row
        
        # Process each result into paragraphs
        for char, base_char, diacritics, detailed_decomp in two_diacritics_results:

            code_point = f"U+{ord(char):04X}"
            char_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='16'>{char}</font><br/><font size='8'>{code_point}</font></para>", custom_style)
            
            base_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='14'>{base_char}</font></para>", simple_decomp_style)
            
            diacritics_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='14'>{diacritics}</font></para>", simple_decomp_style)

            detailed_decomp_cell = Paragraph(f"<font face='{main_font}'>{detailed_decomp}</font>", detailed_decomp_style)
            
            table2_data.append([char_cell, base_cell, diacritics_cell, detailed_decomp_cell])
        
        # Create table with four columns
        table2 = Table(table2_data, colWidths=[80, 70, 70, 310])
        
        # Style the table
        table2_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#32CCCC')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),  # Center align header text
            ('ALIGN', (0, 1), (-1, -1), 'LEFT'),   # Left align content text
            ('FONTNAME', (0, 0), (-1, 0), bold_font),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
        ])
        
        # Add alternating row colors
        for i in range(1, len(table2_data)):
            if i % 2 == 0:
                table2_style.add('BACKGROUND', (0, i), (-1, i), colors.lightgrey)
        
        table2.setStyle(table2_style)
        content.append(table2)
    else:
        content.append(Paragraph("No characters with two or more diacritic marks were found.", custom_style))
    
    # Add space between tables
    content.append(Spacer(1, 30))
    
    # ===== TABLE 3: Other occurrences in the Latin RZ LGR =====
    content.append(Paragraph(f"Other occurrences in the Latin RZ LGR ({len(other_lgr_occurrences)})", heading2_style))
    
    # Create table data for other LGR occurrences
    table3_data = [["Character", "Base", "Diacritic", "Technical Details"]]  # Header row
    
    # Process each result into paragraphs
    for char, base_char, diacritic, detailed_decomp in other_lgr_occurrences:
        # For the character column, show both the character and its code point
        # We need to calculate the code point of the combined character
        combined_code_point_str = format_code_point_string(char)
        
        char_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='16'>{char}</font><br/><font size='8'>{combined_code_point_str}</font></para>", custom_style)
        
        base_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='14'>{base_char}</font></para>", simple_decomp_style)
        
        diacritic_cell = Paragraph(f"<para align='center'><font face='{main_font}' size='14'>{diacritic}</font></para>", simple_decomp_style)
        
        detailed_decomp_cell = Paragraph(f"<font face='{main_font}'>{detailed_decomp}</font>", detailed_decomp_style)
        
        table3_data.append([char_cell, base_cell, diacritic_cell, detailed_decomp_cell])
    
    # Create table with four columns
    table3 = Table(table3_data, colWidths=[80, 70, 70, 310])
    
    # Style the table
    table3_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#32CCCC')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),  # Center align header text
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),   # Left align content text
        ('FONTNAME', (0, 0), (-1, 0), bold_font),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
    ])
    
    # Add alternating row colors
    for i in range(1, len(table3_data)):
        if i % 2 == 0:
            table3_style.add('BACKGROUND', (0, i), (-1, i), colors.lightgrey)
    
    table3.setStyle(table3_style)
    content.append(table3)

    # Coverage summary
    content.append(Spacer(1, 20))
    summary_text = (
        f"Coverage Summary — Latin repertoire single code points: {coverage_summary.get('total_points', '?')}; "
        f"in-scope (ASCII base + combining): {coverage_summary.get('in_scope', '?')}; "
        f"out-of-scope (indexed below): {coverage_summary.get('out_of_scope', '?')}; "
        f"sequences in LGR: {coverage_summary.get('total_sequences', '?')}; "
        f"sequences shown (ASCII base): {coverage_summary.get('ascii_base_sequences', '?')}."
    )

    if thesis_sections:
        thesis_summary = '; '.join(
            f"{section['flag']}: {len(section.get('entries', []))}"
            for section in thesis_sections
        )
        summary_text += f" Thesis sections enabled — {thesis_summary}."

    content.append(Paragraph(summary_text, custom_style))

    # Compact appendix: out-of-scope index
    content.append(Spacer(1, 12))
    content.append(Paragraph(f"Appendix: Out of scope under the current thesis ({len(out_of_scope_index)})", heading2_style))

    appendix_data = [["Glyph", "Code point", "Name"]]
    for ch, cp, name in out_of_scope_index:
        appendix_data.append([
            Paragraph(f"<para align='center'><font face='{main_font}' size='12'>{ch}</font></para>", detailed_decomp_style),
            Paragraph(f"{cp}", detailed_decomp_style),
            Paragraph(f"{name}", detailed_decomp_style),
        ])

    appendix_table = Table(appendix_data, colWidths=[60, 90, 380])
    appendix_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E0F7F7')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, -1), main_font),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])
    appendix_table.setStyle(appendix_style)
    content.append(appendix_table)
    
    # Build PDF
    doc.build(content)
    
    return output_filename

def main():
    """Main execution function."""
    try:
        cli_options = parse_cli_args(sys.argv)
        enabled_thesis_flags = cli_options['enabled_thesis_flags']
        json_output = cli_options['json_output']
        json_only = cli_options['json_only']

        # Step 1: Parse normative XML (Latin RZ-LGR) — authoritative repertoire
        latin_points, latin_sequences, blocked_variants = parse_lgr_xml(XML_URL)
        characters = [chr(cp) for cp in latin_points]
        
        # Step 2: Set up temporary database
        conn = setup_temp_database()
        
        # Step 3: Store data in database
        store_data_in_db(characters, conn)
        
        # Step 4: Analyze characters
        results_tuple = analyze_characters(conn)
        one_diacritic_results, two_diacritics_results = results_tuple

        # Derive sequences (ASCII base) from XML repertoire sequences
        sequences_ascii_base = classify_sequences_ascii_base(latin_sequences)

        # Collect requested thesis sections
        thesis_sections = collect_requested_thesis_sections(conn, enabled_thesis_flags, blocked_variants)
        web_thesis_sections = collect_requested_thesis_sections(conn, list(THESIS_FLAGS.keys()), blocked_variants)
        web_thesis_sections_by_flag = {
            section['flag']: section
            for section in web_thesis_sections
        }

        # Build out-of-scope index and coverage counts for the currently enabled thesis flags
        current_scope_snapshot = build_scope_snapshot(conn, thesis_sections)
        out_of_scope_index = current_scope_snapshot['out_of_scope_index']
        base_counts = current_scope_snapshot['coverage_counts']
        coverage_summary = {
            'total_points': base_counts.get('total_points', 0),
            'in_scope': base_counts.get('in_scope', 0),
            'out_of_scope': base_counts.get('out_of_scope', 0),
            'total_sequences': len(latin_sequences),
            'ascii_base_sequences': len(sequences_ascii_base),
        }
        
        # Print summary to console
        total_results = len(one_diacritic_results) + len(two_diacritics_results)
        print(f"Found {total_results} Latin characters with ASCII base + diacritics")

        for thesis_section in thesis_sections:
            print(
                f"Added thesis section {thesis_section['flag']} "
                f"with {len(thesis_section['entries'])} characters"
            )

        if json_output:
            payload = build_web_report_payload(
                conn,
                results_tuple,
                sequences_ascii_base,
                latin_sequences,
                web_thesis_sections_by_flag,
            )
            json_path = write_web_json_report(json_output, payload)
            print(f"Web JSON data saved to: {json_path}")
        
        if not json_only:
            # Step 5: Generate PDF report
            pdf_path = generate_pdf_report(
                results_tuple,
                sequences_ascii_base,
                out_of_scope_index,
                coverage_summary,
                PDF_OUTPUT,
                thesis_sections=thesis_sections,
            )
            print(f"Analysis complete! PDF report saved to: {pdf_path}")
        elif json_output:
            print("Analysis complete! JSON-only mode finished successfully.")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup (database is in-memory only)
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    main()
