"""Add platform connections and source freshness tracking (M2 Phase 1)."""
from pathlib import Path
from alembic import op

revision='0003'
down_revision='0002'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than drop platform connection state.')
