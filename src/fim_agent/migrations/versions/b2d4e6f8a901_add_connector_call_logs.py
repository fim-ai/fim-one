"""add connector_call_logs table

Revision ID: b2d4e6f8a901
Revises: a1c2d3e4f567
Create Date: 2026-03-05 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d4e6f8a901'
down_revision: Union[str, None] = 'a1c2d3e4f567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'connector_call_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('connector_id', sa.String(36), sa.ForeignKey('connectors.id'), nullable=False),
        sa.Column('connector_name', sa.String(200), nullable=False),
        sa.Column('action_id', sa.String(36), nullable=True),
        sa.Column('action_name', sa.String(200), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), nullable=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('agent_id', sa.String(36), nullable=True),
        sa.Column('request_method', sa.String(10), nullable=False),
        sa.Column('request_url', sa.String(1000), nullable=False),
        sa.Column('response_status', sa.Integer, nullable=True),
        sa.Column('response_time_ms', sa.Integer, nullable=True),
        sa.Column('success', sa.Boolean, nullable=False),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_connector_call_logs_connector_id', 'connector_call_logs', ['connector_id'])
    op.create_index('ix_connector_call_logs_conversation_id', 'connector_call_logs', ['conversation_id'])
    op.create_index('ix_connector_call_logs_user_id', 'connector_call_logs', ['user_id'])
    op.create_index('ix_connector_call_logs_created_at', 'connector_call_logs', ['created_at'])

    # Backfill NULL model_name on existing conversations from their agent's model config
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("""
            UPDATE conversations SET model_name = (
                SELECT json_extract(agents.model_config_json, '$.model_name')
                FROM agents WHERE agents.id = conversations.agent_id
            ) WHERE model_name IS NULL AND agent_id IS NOT NULL
        """)
    else:
        op.execute("""
            UPDATE conversations SET model_name = (
                SELECT agents.model_config_json::json->>'model_name'
                FROM agents WHERE agents.id = conversations.agent_id
            ) WHERE model_name IS NULL AND agent_id IS NOT NULL
        """)


def downgrade() -> None:
    op.drop_table('connector_call_logs')
