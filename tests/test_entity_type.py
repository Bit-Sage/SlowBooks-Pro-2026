"""Legal entity type (#488).

`entity_info` is the one table that maps an entity type (and, for an LLC, its
tax election) to a label and the federal return it files. Schedule C is
offered only to a sole proprietor or a single-member LLC taxed as one; the
other entities answer 409 at the Schedule C routes, and an unset entity type
leaves Schedule C exactly as before.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

from app.services.settings_service import entity_info

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_JS = ROOT / "app/static/js/settings.js"


# entity_type, llc_tax_election -> label, return_form, schedule_c
ENTITY_TABLE = [
    ("", "", "", "", False),
    ("sole_proprietor", "", "a sole proprietor", "Schedule C", True),
    ("general_partnership", "", "a general partnership", "Form 1065", False),
    ("limited_partnership", "", "a limited partnership", "Form 1065", False),
    ("llp", "", "a limited liability partnership", "Form 1065", False),
    ("c_corp", "", "a C corporation", "Form 1120", False),
    ("s_corp", "", "an S corporation", "Form 1120-S", False),
    ("llc", "disregarded", "an LLC taxed as a sole proprietor", "Schedule C", True),
    ("llc", "partnership", "an LLC taxed as a partnership", "Form 1065", False),
    ("llc", "s_corp", "an LLC taxed as an S corporation", "Form 1120-S", False),
    ("llc", "c_corp", "an LLC taxed as a C corporation", "Form 1120", False),
    # an LLC with no election yet: no return we can name, so Schedule C stays
    ("llc", "", "an LLC", "", False),
]


def test_entity_info_table():
    for entity_type, election, label, return_form, schedule_c in ENTITY_TABLE:
        info = entity_info({"entity_type": entity_type, "llc_tax_election": election})
        assert info["label"] == label, entity_type
        assert info["return_form"] == return_form, entity_type
        assert info["schedule_c"] == schedule_c, entity_type


def test_entity_info_ignores_a_stray_election_for_non_llc():
    # llc_tax_election is only meaningful for an LLC; a leftover value from a
    # company that was an LLC and switched is ignored.
    info = entity_info({"entity_type": "s_corp", "llc_tax_election": "disregarded"})
    assert info["return_form"] == "Form 1120-S"
    assert info["election"] == ""


def test_entity_info_handles_none_and_unset():
    assert entity_info(None)["return_form"] == ""
    assert entity_info({})["schedule_c"] is False


def test_entity_info_nonprofit_files_990():
    # A nonprofit files Form 990 and never Schedule C, whatever entity type it
    # has picked. A nonprofit that chose C corporation must not be told it
    # files Form 1120.
    info = entity_info({"company_type": "nonprofit", "entity_type": "c_corp"})
    assert info["return_form"] == "Form 990"
    assert info["schedule_c"] is False
    assert info["label"] == "a nonprofit"
    # even with no entity type chosen, a nonprofit is gated on Form 990
    assert entity_info({"company_type": "nonprofit"})["return_form"] == "Form 990"


# ---------------------------------------------------------------------------
# The Schedule C gate
# ---------------------------------------------------------------------------


def test_schedule_c_refuses_an_s_corp(client):
    client.put("/api/settings", json={"entity_type": "s_corp"})
    for path in ("/api/tax/schedule-c", "/api/tax/schedule-c/csv"):
        r = client.get(path)
        assert r.status_code == 409, path
        detail = r.json()["detail"]
        assert detail["code"] == "not_schedule_c"
        assert "Form 1120-S" in detail["message"]
        assert "Schedule C" in detail["message"]


def test_schedule_c_refuses_an_llc_taxed_as_a_partnership(client):
    client.put(
        "/api/settings",
        json={"entity_type": "llc", "llc_tax_election": "partnership"},
    )
    r = client.get("/api/tax/schedule-c")
    assert r.status_code == 409
    assert r.json()["detail"]["message"].startswith(
        "This company is an LLC taxed as a partnership; it files Form 1065"
    )


def test_schedule_c_passes_for_a_sole_proprietor(client, seed_accounts):
    client.put("/api/settings", json={"entity_type": "sole_proprietor"})
    assert client.get("/api/tax/schedule-c").status_code == 200


def test_schedule_c_passes_for_a_disregarded_llc(client, seed_accounts):
    client.put(
        "/api/settings",
        json={"entity_type": "llc", "llc_tax_election": "disregarded"},
    )
    assert client.get("/api/tax/schedule-c").status_code == 200


def test_schedule_c_unset_entity_type_is_as_before(client, seed_accounts):
    assert client.get("/api/tax/schedule-c").status_code == 200
    # an LLC with no election chosen also stays open (we can't name a return)
    client.put("/api/settings", json={"entity_type": "llc"})
    assert client.get("/api/tax/schedule-c").status_code == 200


def test_schedule_c_refuses_a_nonprofit_with_form_990(client):
    # A nonprofit that picked C corporation still files Form 990, not 1120.
    client.put(
        "/api/settings", json={"company_type": "nonprofit", "entity_type": "c_corp"}
    )
    r = client.get("/api/tax/schedule-c")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "not_schedule_c"
    assert "Form 990" in detail["message"]
    assert "Form 1120" not in detail["message"]
    # a nonprofit's return follows the company type, so point it there
    assert "Change the company type" in detail["message"]
    assert "entity type" not in detail["message"]


def test_tax_mappings_still_work_for_an_s_corp(client):
    # The 409 gate is only on the Schedule C routes; the tax mappings list
    # (its own toolbar action) stays reachable for every entity.
    client.put("/api/settings", json={"entity_type": "s_corp"})
    assert client.get("/api/tax/mappings").status_code == 200


def test_llc_election_is_cleared_when_entity_is_not_an_llc(client):
    # The hidden "Taxed as" select still submits its last value; a non-LLC must
    # not keep a stale election (#488).
    client.put(
        "/api/settings",
        json={"entity_type": "llc", "llc_tax_election": "disregarded"},
    )
    client.put(
        "/api/settings",
        json={"entity_type": "s_corp", "llc_tax_election": "disregarded"},
    )
    assert client.get("/api/settings").json()["llc_tax_election"] == ""


def test_bad_entity_type_422_does_not_list_the_empty_member(client):
    # "" is a valid (unset) value but naming it reads "must be one of: ,
    # c_corp, ..."; it is filtered out of the message (#488).
    r = client.put("/api/settings", json={"entity_type": "banana"})
    assert r.status_code == 422
    message = r.json()["detail"]
    assert "must be one of: " in message
    assert "one of: ," not in message
    assert ", ," not in message


def test_settings_returns_the_computed_entity_object(client):
    # The server returns entity_info() as a computed `entity` object on the
    # settings GET/PUT, so the UI reads one table rather than copying it (#488).
    put = client.put("/api/settings", json={"entity_type": "s_corp"}).json()
    assert put["entity"]["return_form"] == "Form 1120-S"
    assert put["entity"]["schedule_c"] is False
    get = client.get("/api/settings").json()
    assert get["entity"]["label"] == "an S corporation"


# ---------------------------------------------------------------------------
# The LLC "Taxed as" select only renders for an LLC (source assertions)
# ---------------------------------------------------------------------------


class _Nesting(HTMLParser):
    """Record each id'd element's ancestor ids as the markup is parsed."""

    _VOID = {"input", "img", "br", "hr", "meta", "link"}

    def __init__(self):
        super().__init__()
        self._stack = []  # (tag, id)
        self.ancestors = {}  # id -> set of ancestor ids

    def handle_starttag(self, tag, attrs):
        eid = dict(attrs).get("id")
        if eid is not None:
            self.ancestors[eid] = {i for _, i in self._stack if i}
        if tag not in self._VOID:
            self._stack.append((tag, eid))

    def handle_endtag(self, tag):
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag:
                del self._stack[idx:]
                break


def test_fiscal_year_field_is_not_inside_the_llc_group():
    # The rebase put main's "First month of fiscal year" group next to this
    # branch's LLC group; closing the LLC group too late would nest the fiscal
    # field inside #llc-election-group and hide it for everyone who is not an
    # LLC. Parse the Company Information markup and check the nesting for real.
    js = SETTINGS_JS.read_text()
    start = js.rindex(
        "<div", 0, js.index('aria-labelledby="settings-h-company-information"')
    )
    end = js.index('<div class="settings-section"', start + 1)
    markup = re.sub(r"\$\{[^}]*\}", "", js[start:end])
    parser = _Nesting()
    parser.feed(markup)
    # the LLC "Taxed as" select belongs inside the LLC group ...
    assert "llc-election-group" in parser.ancestors["llc-tax-election"]
    # ... but the fiscal-year field must not, or it disappears for non-LLCs
    assert "llc-election-group" not in parser.ancestors["fiscal-year-start-month"]


def test_llc_election_select_is_hidden_unless_llc():
    js = SETTINGS_JS.read_text()
    # the group is hidden unless the stored entity type is llc
    assert "id=\"llc-election-group\" ${s.entity_type === 'llc' ? '' : 'hidden'}" in js
    # and the onchange toggles it as the picker changes
    assert "SettingsPage._onEntityTypeChange" in js
    assert "group.hidden = sel.value !== 'llc';" in js
