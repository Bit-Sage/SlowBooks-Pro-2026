"""Drift guard for the IIF type correspondences in iif_common.

The import and export directions of each format used to live in separate
modules with no shared source of truth; nothing failed if one side changed
alone. These tests pin the round-trip invariant: any type the export side
can emit must map back to the same internal enum on import (except the
documented lossy cases inherent to the external vocabulary).
"""

from types import SimpleNamespace

import pytest

from app.models.accounts import BASE_TYPE_OF, DetailType
from app.models.items import ItemType
from app.services.iif_common import (
    IIF_TO_ACCOUNT_TYPE,
    IIF_TO_DETAIL_TYPE,
    IIF_TO_ITEM_TYPE,
    account_to_iif_type,
    item_to_iif_type,
)


def _acct(detail: DetailType):
    return SimpleNamespace(
        account_number="",
        account_type=BASE_TYPE_OF[detail],
        bank_kind=None,
        detail_type=detail.value,
    )


def _item(itype: ItemType):
    return SimpleNamespace(item_type=itype)


# The export reads the detail type back through the import's own table
# (#390): every one of the fifteen goes out as the ACCNTTYPE that imports as
# it, whatever the account's number.
@pytest.mark.parametrize("detail", list(DetailType))
def test_iif_account_type_round_trips(detail):
    iif_type = account_to_iif_type(_acct(detail))
    assert IIF_TO_DETAIL_TYPE[iif_type] == detail
    assert IIF_TO_ACCOUNT_TYPE[iif_type] == BASE_TYPE_OF[detail]


@pytest.mark.parametrize(
    "itype,expected_back",
    [
        (ItemType.SERVICE, ItemType.SERVICE),
        (ItemType.PRODUCT, ItemType.PRODUCT),
        # Lossy: IIF has no MATERIAL type; PART re-imports as PRODUCT.
        (ItemType.MATERIAL, ItemType.PRODUCT),
        (ItemType.LABOR, ItemType.LABOR),
    ],
)
def test_iif_item_type_round_trips(itype, expected_back):
    iif_type = item_to_iif_type(_item(itype))
    assert iif_type in IIF_TO_ITEM_TYPE, f"export emits unknown item type {iif_type}"
    assert IIF_TO_ITEM_TYPE[iif_type] == expected_back
