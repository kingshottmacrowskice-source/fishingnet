"""Add credential/risk recovery state and discovery ledger retention (M2 wrap-up)."""
from pathlib import Path
from alembic import op

revision = '0005'
down_revision = '0004'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than drop recovery state or run ledgers.')
