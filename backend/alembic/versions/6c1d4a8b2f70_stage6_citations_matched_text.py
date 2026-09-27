"""stage6: add citations.matched_text

PROJECT.md Section 5 lists ``citations`` as id, message_id, document_id,
page_number, section, relevance_score - with no excerpt column. But Section 6
defines the citation API object as including ``matched_text``, described as the
*literal substring* from the source chunk because it drives verbatim
highlighting in the viewer.

Those two requirements cannot both hold for persisted history: a
``GET /conversations/{id}`` response is built from the database, and once the
generation-time excerpt is discarded there is no way to reproduce it. Storing it
is the only way to make the documented citation shape true for history as well
as for the live stream.

This is therefore a deliberate, minimal deviation from Section 5: one nullable
column. It is left nullable so rows written before this migration still read
back, and so an answer with no citable excerpt is representable.

Documented here rather than done silently - if the intent was instead that
history returns citations without excerpts, this migration should be reverted
and ``MessageOut`` should treat ``matched_text`` as optional.

Revision ID: 6c1d4a8b2f70
Revises: ab057e3ff53a
Create Date: 2026-09-27

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "6c1d4a8b2f70"
down_revision: str | None = "ab057e3ff53a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "citations",
        sa.Column("matched_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("citations", "matched_text")
