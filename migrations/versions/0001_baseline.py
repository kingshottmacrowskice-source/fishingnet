"""M0/M1 PostgreSQL baseline; no runtime create_all."""
from pathlib import Path
from alembic import op

revision = '0001'
down_revision = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a tested backup.')
