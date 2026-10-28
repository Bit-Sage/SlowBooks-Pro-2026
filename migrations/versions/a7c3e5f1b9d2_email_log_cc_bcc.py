"""email_log gains cc and bcc (#611)

CC and BCC on outgoing email. The log records the copies as sent, so
Recent sends says who else got the mail. Text columns, since the row is
written after the mail has gone out and must hold whatever was sent.
Nullable, nothing backfilled; downgrade drops the two columns.

Revision ID: a7c3e5f1b9d2
Revises: f4c6e8a0b2d4
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7c3e5f1b9d2"
down_revision: Union[str, None] = "f4c6e8a0b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("email_log") as batch:
        batch.add_column(sa.Column("cc", sa.Text(), nullable=True))
        batch.add_column(sa.Column("bcc", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("email_log") as batch:
        batch.drop_column("bcc")
        batch.drop_column("cc")
