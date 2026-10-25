"""Move organizations still on the old default colour to the appace blue.

Only the untouched default (#3D5A80) changes; a colour chosen in Settings is kept.

Revision ID: 7c2e9b41d0a3
Revises: 450e8f013a4f
"""
from alembic import op

revision = '7c2e9b41d0a3'
down_revision = '450e8f013a4f'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE tenants SET accent_color = '#0C67EC' WHERE upper(accent_color) = '#3D5A80'")


def downgrade():
    op.execute("UPDATE tenants SET accent_color = '#3D5A80' WHERE upper(accent_color) = '#0C67EC'")
