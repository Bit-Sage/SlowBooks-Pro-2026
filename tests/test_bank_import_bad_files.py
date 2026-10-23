"""A file the bank import can't read is said so, not a 500.

SlowBooks-Pro-Testing#101 (found in PNWImport's fork): junk posted to
/api/bank-import/preview raised ofxparse's ValueError straight through as a
500. Every bad file now answers 400 with one sentence naming what the
import reads, on the preview and the import alike, and writes nothing.

The bad files here are fixed, not random: a deterministic set that exercises
the parser net — broken markup, nonsense amounts, an absurd date — not just
the binary-file guard an os.urandom blob almost always tripped first."""

import pytest

from app.models.banking import BankTransaction
from app.services.ofx_import import EMPTY_TEXT, UNREADABLE_TEXT

OFX_HEADER = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

"""


def _ofx(trnamt=b"-12.50", second=True, close=True, extra=b""):
    """A small but complete OFX statement, tweakable for the bad cases."""
    body = (
        OFX_HEADER
        + b"<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
        + b"<DTSERVER>20260131<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>\n"
        + b"<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
        + b"<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>121000248<ACCTID>1234\n"
        + b"<ACCTTYPE>CHECKING</BANKACCTFROM>\n"
        + b"<BANKTRANLIST><DTSTART>20260101<DTEND>20260131\n"
        + b"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105<TRNAMT>"
        + trnamt
        + b"<FITID>A1\n<NAME>Coffee Roasters</STMTTRN>\n"
    )
    if second:
        body += (
            b"<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260110<TRNAMT>1250.00<FITID>A2\n"
            b"<NAME>Customer Deposit</STMTTRN>\n"
        )
    body += (
        b"</BANKTRANLIST><LEDGERBAL><BALAMT>1237.50<DTASOF>20260131</LEDGERBAL>\n"
        b"</STMTRS></STMTTRNRS></BANKMSGSRSV1>" + extra
    )
    if close:
        body += b"</OFX>\n"
    return body


GOOD_OFX = _ofx()

# A fixed, repeatable blob of binary junk: every byte value, so it is plainly
# not text and carries NULs for the binary-file guard. Not os.urandom — the
# same bytes every run.
BINARY_JUNK = bytes(range(256)) * 8

BAD_FILES = {
    "binary junk": BINARY_JUNK,
    "truncated OFX": GOOD_OFX[: len(GOOD_OFX) // 2],
    "a PDF": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n",
    "a CSV with no recognizable columns": b"foo,bar,baz\n1,2,3\n4,5,6\n",
    "a UTF-16 file posted as OFX": (
        "Date,Description,Amount\n01/05/2026,Coffee,-12.50\n".encode("utf-16")
    ),
    # Exercise the parser net, not just the binary guard:
    "an OFX amount of 1e999999999": _ofx(trnamt=b"1e999999999"),
    "an OFX amount of NaN": _ofx(trnamt=b"NaN"),
    "an OFX amount of Infinity": _ofx(trnamt=b"Infinity"),
}


@pytest.fixture
def feed(client, seed_accounts):
    return client.post(
        "/api/banking/accounts",
        json={"name": "Community Bank", "account_id": seed_accounts["1000"].id},
    ).json()


def _post(client, path, content, name="statement.ofx"):
    return client.post(
        path, files={"file": (name, content, "application/octet-stream")}
    )


@pytest.mark.parametrize("kind", list(BAD_FILES))
def test_a_bad_file_is_a_400_with_the_sentence(client, db_session, feed, kind):
    content = BAD_FILES[kind]
    for path in ("/api/bank-import/preview", f"/api/bank-import/import/{feed['id']}"):
        r = _post(client, path, content)
        assert r.status_code == 400, (kind, path, r.text)
        assert r.json()["detail"] == UNREADABLE_TEXT
    assert db_session.query(BankTransaction).count() == 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/bank-import/preview",
        "/api/bank-import/import/{id}",
        "/api/bank-import/preview-csv",
        "/api/bank-import/import-csv/{id}",
    ],
)
def test_an_empty_file_says_it_is_empty(client, db_session, feed, path):
    r = _post(client, path.format(id=feed["id"]), b"")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == EMPTY_TEXT
    assert db_session.query(BankTransaction).count() == 0


@pytest.mark.parametrize(
    "encoding,bom",
    [
        ("utf-8-sig", "﻿".encode("utf-8")),
        ("utf-16-le", "﻿".encode("utf-16-le")),
        ("utf-16-be", "﻿".encode("utf-16-be")),
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "/api/bank-import/preview",
        "/api/bank-import/import/{id}",
        "/api/bank-import/preview-csv",
        "/api/bank-import/import-csv/{id}",
    ],
)
def test_a_bom_only_file_says_it_is_empty(
    client, db_session, feed, path, encoding, bom
):
    # A file that is nothing but a byte-order mark decodes to "" — there is no
    # content after the encoding declaration — so it must answer the empty-file
    # sentence (400), not slip past as a 200 or an "unreadable". OFX and CSV
    # alike, on preview and import. Tested for the UTF-8 BOM and both UTF-16
    # BOMs (Testing#101).
    name = "statement.csv" if "csv" in path else "statement.ofx"
    r = _post(client, path.format(id=feed["id"]), bom, name=name)
    assert r.status_code == 400, (encoding, path, r.text)
    assert r.json()["detail"] == EMPTY_TEXT, (encoding, path, r.text)
    assert db_session.query(BankTransaction).count() == 0


def test_a_binary_file_on_the_csv_path_is_a_400_too(client, db_session, feed):
    for path in (
        "/api/bank-import/preview-csv",
        f"/api/bank-import/import-csv/{feed['id']}",
    ):
        r = _post(client, path, BINARY_JUNK, name="statement.csv")
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"] == UNREADABLE_TEXT
    assert db_session.query(BankTransaction).count() == 0


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-8-sig"])
def test_a_utf16_or_bom_csv_is_read_not_refused(client, db_session, feed, encoding):
    # A real CSV export carrying a byte-order mark (UTF-16 LE/BE, or UTF-8
    # with BOM) is decoded by it, not mistaken for binary junk (Testing#101).
    csv_text = "Date,Description,Amount\n01/05/2026,Coffee,-12.50\n"
    if encoding in ("utf-16-le", "utf-16-be"):
        bom = "﻿"  # these codecs don't add the BOM themselves
        content = (bom + csv_text).encode(encoding)
    else:
        content = csv_text.encode(encoding)
    r = _post(client, "/api/bank-import/preview-csv", content, name="statement.csv")
    assert r.status_code == 200, (encoding, r.text)
    assert r.json()["count"] == 1, (encoding, r.text)
    r = _post(
        client,
        f"/api/bank-import/import-csv/{feed['id']}",
        content,
        name="statement.csv",
    )
    assert r.status_code == 200, (encoding, r.text)
    assert r.json()["imported"] == 1
    assert db_session.query(BankTransaction).count() == 1


def test_a_csv_with_an_absurd_date_does_not_500(client, db_session, feed):
    # dateutil raises OverflowError (not ValueError) on a number this big;
    # the row is skipped as unreadable, not turned into a 500 (Testing#101).
    content = b"Date,Description,Amount\n99999999999999999999,Coffee,-12.50\n"
    for path in (
        "/api/bank-import/preview-csv",
        f"/api/bank-import/import-csv/{feed['id']}",
    ):
        r = _post(client, path, content, name="statement.csv")
        assert r.status_code == 200, (path, r.text)
    assert db_session.query(BankTransaction).count() == 0


def test_a_statement_missing_only_its_close_still_imports(client, db_session, feed):
    # Regression: ofxparse reads it fine and returns both transactions; the
    # missing-</OFX> guard must only fire when nothing was read (Testing#101).
    content = _ofx(close=False)
    r = _post(client, "/api/bank-import/preview", content)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    r = _post(client, f"/api/bank-import/import/{feed['id']}", content)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2
    assert db_session.query(BankTransaction).count() == 2


def test_a_statement_with_trailing_nul_padding_still_imports(client, db_session, feed):
    # Regression: trailing NULs are stripped before the binary-file guard, so
    # a padded-but-complete statement imports both transactions (Testing#101).
    content = GOOD_OFX + b"\x00" * 128
    r = _post(client, "/api/bank-import/preview", content)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    r = _post(client, f"/api/bank-import/import/{feed['id']}", content)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2
    assert db_session.query(BankTransaction).count() == 2


def test_a_csv_the_reader_chokes_on_is_a_400(client, db_session, feed):
    # one quoted field past the csv module's field limit raises csv.Error
    content = b'"' + b"a" * 200_000 + b"\n"
    for path in (
        "/api/bank-import/preview-csv",
        f"/api/bank-import/import-csv/{feed['id']}",
    ):
        r = _post(client, path, content, name="statement.csv")
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"] == UNREADABLE_TEXT
    assert db_session.query(BankTransaction).count() == 0


def test_an_unrecognised_csv_still_offers_its_columns(client, feed):
    r = _post(
        client,
        "/api/bank-import/preview-csv",
        BAD_FILES["a CSV with no recognizable columns"],
        name="statement.csv",
    )
    assert r.status_code == 200
    assert r.json()["header_row"]


def test_a_good_ofx_still_previews_and_imports(client, db_session, feed):
    r = _post(client, "/api/bank-import/preview", GOOD_OFX)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    r = _post(client, f"/api/bank-import/import/{feed['id']}", GOOD_OFX)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2


def test_the_size_limit_still_answers_413(client):
    from app.services.upload_limits import MAX_IMPORT_BYTES

    r = _post(client, "/api/bank-import/preview", b"x" * (MAX_IMPORT_BYTES + 1))
    assert r.status_code == 413


# Whether bs4 rejects <![bogus[x]]> depends on its version and the parsers
# installed (it does on one machine, reads past it on CI), so the real file
# is only held to "never a 500", and the rejection itself is forced.
BROKEN_MARKUP = _ofx(extra=b"<![bogus[x]]>")


def test_an_ofx_with_broken_markup_never_500s(client, db_session, feed):
    for path in ("/api/bank-import/preview", f"/api/bank-import/import/{feed['id']}"):
        r = _post(client, path, BROKEN_MARKUP)
        assert r.status_code in (200, 400), (path, r.text)
        if r.status_code == 400:
            assert r.json()["detail"] == UNREADABLE_TEXT


def test_markup_bs4_rejects_is_a_400_with_the_sentence(
    client, db_session, feed, monkeypatch
):
    bs4 = pytest.importorskip("bs4")
    ofxparse = pytest.importorskip("ofxparse")

    def reject(*args, **kwargs):
        raise bs4.ParserRejectedMarkup("bogus markup")

    monkeypatch.setattr(ofxparse.OfxParser, "parse", reject)
    for path in ("/api/bank-import/preview", f"/api/bank-import/import/{feed['id']}"):
        r = _post(client, path, BROKEN_MARKUP)
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"] == UNREADABLE_TEXT
    assert db_session.query(BankTransaction).count() == 0
