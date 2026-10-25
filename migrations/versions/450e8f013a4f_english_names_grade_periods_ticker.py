"""English names, a grade's own periods, homeroom teacher, news ticker

Revision ID: 450e8f013a4f
Revises: 1ee56deff739
Create Date: 2026-10-25 19:25:43

Only ADD COLUMN, never a table rebuild: on SQLite a rebuild of «periods» with foreign keys on would cascade
into the timetable entries.
"""
from alembic import op
import sqlalchemy as sa


revision = '450e8f013a4f'
down_revision = '1ee56deff739'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('class_leave_types', sa.Column('name_en', sa.String(length=100), nullable=True))
    op.add_column('sections', sa.Column('name_en', sa.String(length=50), nullable=True))
    op.add_column('sections', sa.Column('homeroom_teacher', sa.String(length=200), nullable=True))
    op.add_column('semesters', sa.Column('name_en', sa.String(length=100), nullable=True))
    op.add_column('tenants', sa.Column('ticker_json', sa.Text(), nullable=True))
    op.add_column('periods', sa.Column('label_en', sa.String(length=100), nullable=True))
    if op.get_bind().dialect.name == 'sqlite':
        op.execute('ALTER TABLE periods ADD COLUMN grade_id INTEGER REFERENCES grades (id) ON DELETE CASCADE')
    else:
        op.add_column('periods', sa.Column('grade_id', sa.Integer(), nullable=True))
        op.create_foreign_key('fk_periods_grade_id', 'periods', 'grades', ['grade_id'], ['id'], ondelete='CASCADE')
    op.create_index('ix_periods_grade_id', 'periods', ['grade_id'], unique=False)


def downgrade():
    op.drop_index('ix_periods_grade_id', table_name='periods')
    with op.batch_alter_table('periods') as b:
        b.drop_column('grade_id')
        b.drop_column('label_en')
    for table, col in (('tenants', 'ticker_json'), ('semesters', 'name_en'), ('sections', 'homeroom_teacher'),
                       ('sections', 'name_en'), ('class_leave_types', 'name_en')):
        with op.batch_alter_table(table) as b:
            b.drop_column(col)
