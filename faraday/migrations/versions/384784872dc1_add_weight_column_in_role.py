"""Add weight column in role

Revision ID: 384784872dc1
Revises: b31fa447f00c
Create Date: 2022-06-08 19:16:46.442418+00:00

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '384784872dc1'
down_revision = 'b31fa447f00c'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('faraday_role', sa.Column('weight', sa.Integer(), nullable=True))
    op.execute("UPDATE faraday_role set weight=40 WHERE name='admin'")
    op.execute("UPDATE faraday_role set weight=30 WHERE name='asset_owner'")
    op.execute("UPDATE faraday_role set weight=20 WHERE name='pentester'")
    op.execute("UPDATE faraday_role set weight=10 WHERE name='client'")
    op.alter_column('faraday_role', 'weight', nullable=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('faraday_role', 'weight')
    # ### end Alembic commands ###
