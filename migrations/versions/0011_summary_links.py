"""Key links (repo / official site / download) preserved on each video summary."""
from pathlib import Path
from alembic import op

revision = '0011'
down_revision = '0010'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute('ALTER TABLE video_summaries DROP COLUMN links')
