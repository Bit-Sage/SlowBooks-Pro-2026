"""lien_waivers: one waiver per payment, job and type (#576)

A payment split across two jobs' invoices gets a waiver per job, each for the
part applied to that job, so the unique key widens from (payment, type) to
(payment, job, type). Existing rows keep their job and amount.

Revision ID: b5d7e9f1a3c5
Revises: b5d7f9a1c3e5
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b5d7e9f1a3c5"
down_revision: Union[str, None] = "b5d7f9a1c3e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _unique_names(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_unique_constraints(table)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "lien_waivers" not in insp.get_table_names():
        return
    names = _unique_names("lien_waivers")
    if "uq_lien_waiver_payment_job_type" in names:
        return
    with op.batch_alter_table("lien_waivers") as batch:
        if "uq_lien_waiver_payment_type" in names:
            batch.drop_constraint("uq_lien_waiver_payment_type", type_="unique")
        batch.create_unique_constraint(
            "uq_lien_waiver_payment_job_type", ["payment_id", "job_id", "waiver_type"]
        )


def downgrade() -> None:
    with op.batch_alter_table("lien_waivers") as batch:
        batch.drop_constraint("uq_lien_waiver_payment_job_type", type_="unique")
        batch.create_unique_constraint(
            "uq_lien_waiver_payment_type", ["payment_id", "waiver_type"]
        )
