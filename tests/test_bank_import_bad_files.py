"""A file the bank import can't read is said so, not a 500.

SlowBooks-Pro-Testing#101 (found in PNWImport's fork): junk posted to
/api/bank-import/preview raised ofxparse's ValueError straight through as a
500. Every bad file now answers 400 with one sentence naming what the
import reads, on the preview and the import alike, and writes nothing."""

import os

import pytest

from app.models.banking import BankTransaction
from app.services.ofx_import import EMPTY_TEXT, UNREADABLE_TEXT

GOOD_OFX = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS>
<DTSERVER>20260131<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>121000248<ACCTID>1234
<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260101<DTEND>20260131
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105<TRNAMT>-12.50<FITID>A1
<NAME>Coffee Roasters</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260110<TRNAMT>1250.00<FITID>A2
<NAME>Customer Deposit</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>1237.50<DTASOF>20260131</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

BAD_FILES = {
    "random bytes": os.urandom(4096),
    "truncated OFX": GOOD_OFX[: len(GOOD_OFX) // 2],
    "a PDF": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n",
    "a CSV with no recognizable columns": b"foo,bar,baz\n1,2,3\n4,5,6\n",
    "a UTF-16 file": "Date,Description,Amount\n01/05/2026,Coffee,-12.50\n".encode(
        "utf-16"
    ),
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


@pytest.mark.parametrize("kind", ["random bytes", "a UTF-16 file"])
def test_a_binary_file_on_the_csv_path_is_a_400_too(client, db_session, feed, kind):
    for path in (
        "/api/bank-import/preview-csv",
        f"/api/bank-import/import-csv/{feed['id']}",
    ):
        r = _post(client, path, BAD_FILES[kind], name="statement.csv")
        assert r.status_code == 400, (kind, path, r.text)
        assert r.json()["detail"] == UNREADABLE_TEXT
    assert db_session.query(BankTransaction).count() == 0


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
