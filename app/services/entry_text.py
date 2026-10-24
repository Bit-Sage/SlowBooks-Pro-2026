"""The words a report shows for a posting's header.

A register entry typed with a memo and no payee carries the sentinel
"Bank entry" on its header (bank_posting.bank_entry_lines) so it reopens
with the memo in Memo, not in Payee. That sentinel is a placeholder, not
the entry's words: a report or API that reads the header shows the line's
memo in its place.
"""

BANK_ENTRY_HEADER = "Bank entry"


def is_bank_entry_placeholder(txn) -> bool:
    """True when the header is the no-payee sentinel of a register entry."""
    return (
        getattr(txn, "source_type", None) == "bank_entry"
        and (txn.description or "") == BANK_ENTRY_HEADER
    )


def header_text(txn, line_text: str | None = None) -> str:
    """The header's description, or the line's text in place of the
    register's no-payee sentinel.

    `line_text` is the line being shown (a ledger row); without it the first
    line carrying text stands in (a whole entry, as the journal shows it).
    """
    if is_bank_entry_placeholder(txn):
        if line_text:
            return line_text
        for ln in sorted(getattr(txn, "lines", None) or [], key=lambda ln: ln.id):
            if ln.description:
                return ln.description
        return BANK_ENTRY_HEADER
    return txn.description or line_text or ""
