"""Initial schema.

Revision ID: 0001_initial
Revises: 
Create Date: 2025-01-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suno_url", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("artist_display", sa.String(length=255), nullable=True),
        sa.Column("artist_username", sa.String(length=255), nullable=True),
        sa.Column("lyrics", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("video_url", sa.String(length=1024), nullable=True),
        sa.Column("mp3_url", sa.String(length=1024), nullable=True),
        sa.Column("opus_url", sa.String(length=1024), nullable=True),
        sa.Column("opus_path", sa.String(length=1024), nullable=True),
        sa.Column("opus_status", sa.String(length=32), nullable=True),
        sa.Column("opus_transcoded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suno_url", name="uq_tracks_suno_url"),
    )
    op.create_index("ix_tracks_suno_url", "tracks", ["suno_url"], unique=True)

    op.create_table(
        "jam_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "ended", name="jam_session_status"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jam_sessions_channel_id", "jam_sessions", ["channel_id"], unique=False)
    op.create_index("ix_jam_sessions_guild_id", "jam_sessions", ["guild_id"], unique=False)
    op.create_index("ix_jam_sessions_status", "jam_sessions", ["status"], unique=False)
    op.create_index(
        "ix_jam_sessions_active_guild",
        "jam_sessions",
        ["guild_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submissions_guild_id", "submissions", ["guild_id"], unique=False)

    op.create_table(
        "queue_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["jam_sessions.id"]),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_queue_items_guild_id", "queue_items", ["guild_id"], unique=False)
    op.create_index("ix_queue_items_session_id", "queue_items", ["session_id"], unique=False)
    op.create_index("ix_queue_items_status", "queue_items", ["status"], unique=False)

    op.create_table(
        "session_reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "reaction_type",
            sa.Enum("upvote", "downvote", name="session_reaction_type"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["jam_sessions.id"]),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "track_id",
            "user_id",
            "reaction_type",
            name="uq_session_reactions_unique",
        ),
    )
    op.create_index("ix_session_reactions_session_id", "session_reactions", ["session_id"], unique=False)
    op.create_index("ix_session_reactions_track_id", "session_reactions", ["track_id"], unique=False)
    op.create_index("ix_session_reactions_reaction_type", "session_reactions", ["reaction_type"], unique=False)

    op.create_table(
        "opus_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mp3_url", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", name="uq_opus_jobs_track_id"),
    )
    op.create_index("ix_opus_jobs_status", "opus_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_opus_jobs_status", table_name="opus_jobs")
    op.drop_table("opus_jobs")

    op.drop_index("ix_session_reactions_reaction_type", table_name="session_reactions")
    op.drop_index("ix_session_reactions_track_id", table_name="session_reactions")
    op.drop_index("ix_session_reactions_session_id", table_name="session_reactions")
    op.drop_table("session_reactions")

    op.drop_index("ix_queue_items_status", table_name="queue_items")
    op.drop_index("ix_queue_items_session_id", table_name="queue_items")
    op.drop_index("ix_queue_items_guild_id", table_name="queue_items")
    op.drop_table("queue_items")

    op.drop_index("ix_submissions_guild_id", table_name="submissions")
    op.drop_table("submissions")

    op.drop_index("ix_jam_sessions_active_guild", table_name="jam_sessions")
    op.drop_index("ix_jam_sessions_status", table_name="jam_sessions")
    op.drop_index("ix_jam_sessions_guild_id", table_name="jam_sessions")
    op.drop_index("ix_jam_sessions_channel_id", table_name="jam_sessions")
    op.drop_table("jam_sessions")

    op.drop_index("ix_tracks_suno_url", table_name="tracks")
    op.drop_table("tracks")

    op.execute("DROP TYPE IF EXISTS session_reaction_type")
    op.execute("DROP TYPE IF EXISTS jam_session_status")
