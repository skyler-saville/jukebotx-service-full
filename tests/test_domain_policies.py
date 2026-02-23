from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([
    str(ROOT / "packages" / "core"),
])

from jukebotx_core.domain import (
    QUEUE_STATUS_PLAYED,
    QUEUE_STATUS_PLAYING,
    QUEUE_STATUS_QUEUED,
    QUEUE_STATUS_SKIPPED,
    can_transition_queue_item,
    ensure_queue_transition,
    evaluate_submission_duplication_policy,
)


def test_queue_status_transitions_follow_policy() -> None:
    assert can_transition_queue_item(current_status=QUEUE_STATUS_QUEUED, next_status=QUEUE_STATUS_PLAYING)
    assert can_transition_queue_item(current_status=QUEUE_STATUS_QUEUED, next_status=QUEUE_STATUS_PLAYED)
    assert can_transition_queue_item(current_status=QUEUE_STATUS_PLAYING, next_status=QUEUE_STATUS_PLAYED)
    assert not can_transition_queue_item(current_status=QUEUE_STATUS_PLAYED, next_status=QUEUE_STATUS_QUEUED)
    assert not can_transition_queue_item(current_status=QUEUE_STATUS_SKIPPED, next_status=QUEUE_STATUS_PLAYED)


def test_ensure_queue_transition_raises_for_invalid_transition() -> None:
    with pytest.raises(ValueError):
        ensure_queue_transition(current_status=QUEUE_STATUS_PLAYED, next_status=QUEUE_STATUS_SKIPPED)


def test_submission_duplication_policy() -> None:
    first_submission = evaluate_submission_duplication_policy(
        prior_submission_exists=False,
        auto_enqueue_requested=True,
    )
    assert first_submission.is_duplicate_in_guild is False
    assert first_submission.should_enqueue is True

    duplicate_submission = evaluate_submission_duplication_policy(
        prior_submission_exists=True,
        auto_enqueue_requested=True,
    )
    assert duplicate_submission.is_duplicate_in_guild is True
    assert duplicate_submission.should_enqueue is False
