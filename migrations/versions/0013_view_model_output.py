"""Persist the raw model response text so a malformed output can be inspected."""
from pathlib import Path
from alembic import op

revision = '0013'
down_revision = '0012'


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute('ALTER TABLE ai_invocations DROP COLUMN response_content')
