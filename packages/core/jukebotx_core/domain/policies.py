from __future__ import annotations

from dataclasses import dataclass
from typing import Final


QUEUE_STATUS_QUEUED: Final[str] = "queued"
QUEUE_STATUS_PLAYING: Final[str] = "playing"
QUEUE_STATUS_PLAYED: Final[str] = "played"
QUEUE_STATUS_SKIPPED: Final[str] = "skipped"

TERMINAL_QUEUE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        QUEUE_STATUS_PLAYED,
        QUEUE_STATUS_SKIPPED,
    }
)

_QUEUE_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    QUEUE_STATUS_QUEUED: frozenset({QUEUE_STATUS_PLAYING, QUEUE_STATUS_PLAYED, QUEUE_STATUS_SKIPPED}),
    QUEUE_STATUS_PLAYING: frozenset({QUEUE_STATUS_PLAYED, QUEUE_STATUS_SKIPPED}),
    QUEUE_STATUS_PLAYED: frozenset(),
    QUEUE_STATUS_SKIPPED: frozenset(),
}


def can_transition_queue_item(*, current_status: str, next_status: str) -> bool:
    """Return whether a queue item can move from the current status to the next status."""
    return next_status in _QUEUE_TRANSITIONS.get(current_status, frozenset())


def ensure_queue_transition(*, current_status: str, next_status: str) -> None:
    """Raise ValueError if a queue status transition is invalid."""
    if not can_transition_queue_item(current_status=current_status, next_status=next_status):
        raise ValueError(f"Invalid queue status transition: {current_status!r} -> {next_status!r}")


@dataclass(frozen=True)
class SubmissionDuplicationPolicyDecision:
    is_duplicate_in_guild: bool
    should_enqueue: bool


def evaluate_submission_duplication_policy(
    *,
    prior_submission_exists: bool,
    auto_enqueue_requested: bool,
) -> SubmissionDuplicationPolicyDecision:
    """
    Apply the guild-local submission duplication policy.

    Duplicates are identified per guild. Auto-enqueue is only allowed for first-time
    submissions in that guild.
    """
    is_duplicate = prior_submission_exists
    should_enqueue = auto_enqueue_requested and (not is_duplicate)
    return SubmissionDuplicationPolicyDecision(
        is_duplicate_in_guild=is_duplicate,
        should_enqueue=should_enqueue,
    )
