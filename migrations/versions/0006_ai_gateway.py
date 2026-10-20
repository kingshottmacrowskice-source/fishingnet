"""Add model providers, routing, invocation ledger and budget reservations (M3 phase 1)."""
from pathlib import Path
from alembic import op

revision = '0006'
down_revision = '0005'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than drop provider or cost ledger rows.')
