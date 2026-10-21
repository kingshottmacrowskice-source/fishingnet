"""Video summaries, round reports and mention polling for the focused first version."""
from pathlib import Path
from alembic import op

revision = '0009'
down_revision = '0008'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Restore backup rather than discard summaries.')
