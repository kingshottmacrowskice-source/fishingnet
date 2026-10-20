"""Add expression-support reviews and reviewable model entity links (M3 wrap-up)."""
from pathlib import Path
from alembic import op

revision = '0007'
down_revision = '0006'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than drop expression review evidence.')
