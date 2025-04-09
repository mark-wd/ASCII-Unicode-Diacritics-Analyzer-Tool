#!/usr/bin/env python3
"""
ASCII-Unicode Diacritics Analyzer Tool (v1.1)
On behalf of the ICANN Latin Script Diacritics Policy Development Process WG (LD-WG)

For inquiries about the code, contact the maintainer:
Mark W. Datysgeld (mark@governanceprimer.com)

This utility implements Unicode normalization (NFD) to analyze Latin script code points
from ICANN's Label Generation Rules. It identifies characters that canonically decompose
to ASCII base characters plus combining diacritical marks (Unicode General Category M).
Results are categorized by diacritic count and output to a structured PDF report with
complete Unicode technical data. The implementation uses in-memory SQLite storage and
leaves no temporary files behind.
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
import sqlite3
import unicodedata
import requests
from bs4 import BeautifulSoup
import tempfile
import urllib.request
import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.frames import Frame
from reportlab.lib.colors import blue, black
from io import BytesIO

# Constants
URL = "https://www.icann.org/sites/default/files/lgr/rz-lgr-5-latin-script-26may22-en.html"
# Generate filename with current date in YYYY-MM-DD format
current_date = datetime.date.today().strftime("%Y-%m-%d")
PDF_OUTPUT = f"LD-PDP-ASCII-Unicode-Diacritics-Report-{current_date}.pdf"
ASCII_LETTERS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')


def fetch_data_from_url(url):
    # Fetch HTML data from ICANN's "Root Zone Label Generation Rules for the Latin Script" from 2022-05-26 and parses it to extract character data. Returns a list.
    print(f"Fetching data from {url}...")
    response = requests.get(url)
    response.raise_for_status()  # Raise exception for HTTP errors
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Extract characters from the table
    characters = []
    # Look for tables that might contain character data
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if cells:
                # Look for cells that might contain Unicode characters
                for cell in cells:
                    # Extract text and look for potential Unicode characters
                    text = cell.get_text().strip()
                    # Filter out obvious non-character cells
                    if len(text) <= 2:  # Most Unicode characters are 1-2 chars long
                        for char in text:
                            if ord(char) > 127:  # Non-ASCII characters
                                characters.append(char)
    
    # Remove duplicates while preserving order
    unique_chars = []
    seen = set()
    for char in characters:
        if char not in seen:
            seen.add(char)
            unique_chars.append(char)
    
    print(f"Found {len(unique_chars)} unique characters")
    return unique_chars

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
            
            # Create detailed decomposition with names and code points
            detailed_decomp_parts = []
            for c in nfd_form:
                char_name = unicodedata.name(c, 'UNKNOWN')
                code_point = f"U+{ord(c):04X}"
                
                # Add extra spacing around combining characters to prevent them from combining with surrounding text or appearing too close to parentheses
                if unicodedata.category(c).startswith('M'):
                    # For combining characters, add space before and after
                    formatted_char = f"&nbsp;{c}&nbsp;"
                else:
                    # For base characters, just use as is
                    formatted_char = c
                
                detailed_decomp_parts.append(f"{formatted_char} ({char_name}, {code_point})")
            
            # Use HTML formatting for better spacing around the + sign in detailed decomp
            detailed_decomp = ' &nbsp;&nbsp;+&nbsp;&nbsp; '.join(detailed_decomp_parts)
            
            # Add to appropriate result list based on number of diacritics
            if len(diacritics) == 1:
                one_diacritic_results.append((char, base_char, diacritics, detailed_decomp))
            else:
                two_diacritics_results.append((char, base_char, diacritics, detailed_decomp))
    
    conn.commit()
    return (one_diacritic_results, two_diacritics_results)

def process_other_latin_lgr_occurrences():
    """
    Process specific Unicode patterns for the "Other occurrences in the Latin RZ LGR" table.
    
    Returns:
        list: List of tuples containing (combined_char, base_char, diacritic, detailed_decomp)
    """
    # List of Unicode patterns to process
    patterns = [
        "U+0061 U+0331",  # a + combining macron below
        "U+0065 U+0331",  # e + combining macron below
        "U+0067 U+0303",  # g + combining tilde
        "U+0069 U+0331",  # i + combining macron below
        "U+006D U+0327",  # m + combining cedilla
        "U+006E U+0304",  # n + combining macron
        "U+006E U+0308",  # n + combining diaeresis
        "U+006F U+0327",  # o + combining cedilla
        "U+006F U+0331",  # o + combining macron below
        "U+0072 U+0303",  # r + combining tilde
        "U+1EB9 U+0300",  # e with dot below + combining grave accent
        "U+1EB9 U+0301",  # e with dot below + combining acute accent
        "U+1ECD U+0300",  # o with dot below + combining grave accent
        "U+1ECD U+0301",  # o with dot below + combining acute accent
    ]
    
    results = []
    
    for pattern in patterns:
        # Split the pattern into code points
        code_points = pattern.split()
        
        # Convert code points to characters
        chars = [chr(int(cp[2:], 16)) for cp in code_points]
        
        # Combine the characters
        combined_char = ''.join(chars)
        
        # Get the base character and diacritic
        base_char = chars[0]
        diacritic = chars[1]
        
        # Create detailed decomposition with names and code points
        detailed_decomp_parts = []
        for c in chars:
            char_name = unicodedata.name(c, 'UNKNOWN')
            code_point = f"U+{ord(c):04X}"
            
            # Add extra spacing around combining characters
            if unicodedata.category(c).startswith('M'):
                formatted_char = f"&nbsp;{c}&nbsp;"
            else:
                formatted_char = c
            
            detailed_decomp_parts.append(f"{formatted_char} ({char_name}, {code_point})")
        
        # Use HTML formatting for better spacing
        detailed_decomp = ' &nbsp;&nbsp;+&nbsp;&nbsp; '.join(detailed_decomp_parts)
        
        results.append((combined_char, base_char, diacritic, detailed_decomp))
    
    return results

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

def generate_pdf_report(results_tuple, output_filename):
    """
    Generate a PDF report with the analysis results.
    Args:
        results_tuple (tuple): Tuple containing two lists of character data
        output_filename (str): Name of the output PDF file
    """
    one_diacritic_results, two_diacritics_results = results_tuple
    
    # Process other Latin LGR occurrences
    other_lgr_occurrences = process_other_latin_lgr_occurrences()
    
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
    explanation = "This report was generated using the ASCII-Unicode Diacritics Analyzer Tool (v1.1), and can be generated by any other interested party with <a href='https://github.com/mark-wd/ASCII-Unicode-Diacritics-Analyzer-Tool/tree/main' color='blue'>the tool's Python source code in Github</a>, released under 'The Unlicense', equivalent to Public Domain. This software was developed independently by a community member, with no official affiliation with or endorsement by the ICANN organization.<br/><br/>The tool implements Unicode normalization (NFD) to analyze Latin script code points from ICANN's <a href='https://www.icann.org/sites/default/files/lgr/rz-lgr-5-latin-script-26may22-en.html' color='blue'>Label Generation Rules</a> and identifies characters that canonically decompose to ASCII base characters plus combining diacritical marks (Unicode General Category M). Results are categorized by diacritic count and output to this structured PDF report with complete Unicode technical data.<br/><br/>For inquiries about the code, contact the maintainer:<br/>Mark W. Datysgeld (mark@governanceprimer.com)"
    
    content.append(Spacer(1, 20))
    content.append(Paragraph(explanation, custom_style))
    
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
        combined_code_points = [f"U+{ord(c):04X}" for c in char]
        combined_code_point_str = " ".join(combined_code_points)
        
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
    
    # Build PDF
    doc.build(content)
    
    return output_filename

def main():
    """Main execution function."""
    try:
        # Step 1: Fetch data from URL
        characters = fetch_data_from_url(URL)
        
        # Step 2: Set up temporary database
        conn = setup_temp_database()
        
        # Step 3: Store data in database
        store_data_in_db(characters, conn)
        
        # Step 4: Analyze characters
        results_tuple = analyze_characters(conn)
        one_diacritic_results, two_diacritics_results = results_tuple
        
        # Print summary to console
        total_results = len(one_diacritic_results) + len(two_diacritics_results)
        print(f"Found {total_results} Latin characters with ASCII base + diacritics")
        
        # Step 5: Generate PDF report
        pdf_path = generate_pdf_report(results_tuple, PDF_OUTPUT)
        
        print(f"Analysis complete! PDF report saved to: {pdf_path}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup (database is in-memory only)
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    main()
