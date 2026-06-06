"""Test the 'stale' status added to the dashboard favorite-model view.

Background: with the new default regular-model interval of 900s
(15 minutes), a model the user barely uses may not have been
probed for many minutes. The previous rendering treated
"not probed recently" the same as "just succeeded", which
misled the user into thinking those models were fine.

Contract:
  - A model whose `status` is `online` AND whose `last_checked_at`
    is older than STALE_THRESHOLD_MULTIPLIER × effective_interval
    must be downgraded to status `stale`.
  - A model whose status is anything other than `online` (offline,
    unauthorized, not_found, etc.) is left alone — we don't want
    to "stale-ify" a known-broken model.
  - A model that was just probed (< threshold ago) keeps its
    `online` status.
  - A model that has never been probed (`last_checked_at` is None)
    gets status `stale` so the UI surfaces it as a candidate for
    manual probing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.services.results import STALE_THRESHOLD_MULTIPLIER, _is_model_stale


class TestStaleClassification:
    def test_freshly_probed_online_is_not_stale(self):
        # Just probed 30 seconds ago, interval=60 → 30 < 60 × 2 = 120
        assert _is_model_stale(
            status="online",
            last_checked_at=datetime.now(UTC) - timedelta(seconds=30),
            interval_seconds=60,
        ) is False

    def test_old_online_is_stale(self):
        # Last probed 5 minutes ago, interval=60 → 300 > 60 × 2 = 120
        assert _is_model_stale(
            status="online",
            last_checked_at=datetime.now(UTC) - timedelta(seconds=300),
            interval_seconds=60,
        ) is True

    def test_never_probed_is_stale(self):
        assert _is_model_stale(
            status="online",
            last_checked_at=None,
            interval_seconds=60,
        ) is True

    def test_offline_model_does_not_become_stale(self):
        """A model we *know* is broken (offline / unauthorized /
        not_found) must not be re-labelled as 'stale' — that would
        hide a real problem behind a 'just slow' indicator.
        """
        for bad_status in ("offline", "unauthorized", "not_found", "rate_limited", "suspect"):
            assert _is_model_stale(
                status=bad_status,
                last_checked_at=datetime.now(UTC) - timedelta(hours=1),
                interval_seconds=60,
            ) is False, f"{bad_status} should not be re-labelled stale"

    def test_disabled_model_does_not_become_stale(self):
        """Disabled models are intentionally un-probed; calling
        them 'stale' would be confusing.
        """
        assert _is_model_stale(
            status="disabled",
            last_checked_at=None,
            interval_seconds=60,
        ) is False

    def test_regular_long_interval_does_not_flag_as_stale_prematurely(self):
        """A regular model with 900s interval: 1700s is below 2×900s
        = 1800s, so still fresh. Catches the case where someone sets
        a long interval and the stale check fires too aggressively.
        """
        assert _is_model_stale(
            status="online",
            last_checked_at=datetime.now(UTC) - timedelta(seconds=1700),
            interval_seconds=900,
        ) is False

    def test_stale_threshold_multiplier_is_two(self):
        """The stale threshold is documented as 2× the effective
        interval. Pinning this constant so a future tweak is
        intentional.
        """
        assert STALE_THRESHOLD_MULTIPLIER == 2
