"""Single per-day digest for the focused first version."""
from pathlib import Path
from alembic import op

revision = '0010'
down_revision = '0009'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute('DROP TABLE daily_digests')
