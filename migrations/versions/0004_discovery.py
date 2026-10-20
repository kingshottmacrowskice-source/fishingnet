"""Add keyword discovery sources, runs, decisions and job checkpoints (M2 Phase 2)."""
from pathlib import Path
from alembic import op

revision = '0004'
down_revision = '0003'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than drop discovery run history or watermarks.')
