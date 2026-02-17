"""allow_multiple_failures_per_job

Revision ID: 010496b88f78
Revises: 22b5b9f8b2c5
Create Date: 2026-02-13 14:32:08.931286

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '010496b88f78'
down_revision: Union[str, None] = '22b5b9f8b2c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite doesn't support dropping constraints directly, so we need to recreate the table
    # Create a new table without the unique constraint
    op.create_table(
        'failures_new',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),  # No UNIQUE constraint
        sa.Column('failure_category', sa.String(50), nullable=True),
        sa.Column('failure_type', sa.String(100), nullable=True),
        sa.Column('failing_test', sa.String(500), nullable=True),
        sa.Column('error_signature', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('root_cause', sa.Text(), nullable=True),
        sa.Column('is_flaky', sa.Boolean(), nullable=True),
        sa.Column('log_excerpt', sa.Text(), nullable=True),
        sa.Column('resolved_by_pr', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Copy data
    op.execute('INSERT INTO failures_new SELECT * FROM failures')

    # Drop old table
    op.drop_table('failures')

    # Rename new table
    op.rename_table('failures_new', 'failures')


def downgrade() -> None:
    # Re-add the unique constraint by recreating the table
    op.create_table(
        'failures_new',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('failure_category', sa.String(50), nullable=True),
        sa.Column('failure_type', sa.String(100), nullable=True),
        sa.Column('failing_test', sa.String(500), nullable=True),
        sa.Column('error_signature', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('root_cause', sa.Text(), nullable=True),
        sa.Column('is_flaky', sa.Boolean(), nullable=True),
        sa.Column('log_excerpt', sa.Text(), nullable=True),
        sa.Column('resolved_by_pr', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id')
    )

    op.execute('INSERT INTO failures_new SELECT * FROM failures')
    op.drop_table('failures')
    op.rename_table('failures_new', 'failures')
