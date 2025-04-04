This utility implements Unicode normalization (NFD) to analyze Latin script code points
from ICANN's Label Generation Rules. It identifies characters that canonically decompose
to ASCII base characters plus combining diacritical marks (Unicode General Category M).
Results are categorized by diacritic count and output to a structured PDF report with
complete Unicode technical data. The implementation uses in-memory SQLite storage and
leaves no temporary files behind.
