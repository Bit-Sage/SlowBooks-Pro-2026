# QuickBooks Desktop Transaction Detail by Account, as a sample

`Transaction_Detail_by_Account.csv` is invented books in the exact layout of QuickBooks Desktop's Transaction
Detail by Account saved as CSV: 28 columns, the labels before Type, an empty column between every field, a heading
row per account with sub-accounts one column in, QuickBooks' Total rows and the TOTAL row, the Clr check glyph in
the Windows code page, CRLF line ends. `make_ledger_fixture.py` writes it. The real report the importer was built
against is a bookkeeper's client's books and is not here.
