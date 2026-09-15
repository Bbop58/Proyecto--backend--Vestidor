"""Add imagen_url to productos table

Revision ID: 1a2b3c4d5e6f
Revises: f6a1b2c3d4e5
Create Date: 2026-09-11 17:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, Sequence[str], None] = 'f6a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('productos', sa.Column('imagen_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('productos', 'imagen_url')
