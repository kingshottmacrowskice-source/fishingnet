"""Strengthen record pointers and expression relation retirement constraints."""
from pathlib import Path
from alembic import op

revision='0002'
down_revision='0001'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than remove integrity constraints.')
