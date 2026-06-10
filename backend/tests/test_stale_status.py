"""Test the 'stale' status added to the dashboard favorite-model view.

Background: with the new default regular-model interval of 900s
(15 minutes), a model the user barely uses may not have been
probed for many minutes. The previous rendering treated
"not probed recently" the same as "just succeeded", which
misled the user into thinking those models were fine.

Contract:
  - A model whose `status` is `online` AND whose `last_checked_at`
    is older than STALE_THRESHOLD_MULTIPLIER × *maximum effective*
    interval must be downgraded to status `stale`.

    The maximum effective interval accounts for the worst-case
    scheduler stretch: base_interval × BACKOFF_MAX (8×) × IDLE (5×).
    A model at peak backoff with no SSE subscriber probes at most
    every base × 40 seconds, so the stale check must allow that
    headroom — otherwise every healthy model becomes stale the
    moment it hits 8× backoff.

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

from datetime import UTC, datetime, timedelta

from app.services.results import STALE_THRESHOLD_MULTIPLIER, _is_model_stale

# Worst-case effective multiplier: BACKOFF_MAX (8) × IDLE_MULTIPLIER (5)
_MAX_EFFECTIVE_MULTIPLIER = 40


class TestStaleClassification:
    def test_freshly_probed_online_is_not_stale(self):
        # Just probed 30s ago, interval=60 → max_eff=2400, threshold=4800
        # 30 < 4800 → not stale
        assert (
            _is_model_stale(
                status="online",
                last_checked_at=datetime.now(UTC) - timedelta(seconds=30),
                interval_seconds=60,
            )
            is False
        )

    def test_old_online_is_stale(self):
        # Last probed 6000s ago, interval=60 → max_eff=2400, threshold=4800
        # 6000 > 4800 → stale
        assert (
            _is_model_stale(
                status="online",
                last_checked_at=datetime.now(UTC) - timedelta(seconds=6000),
                interval_seconds=60,
            )
            is True
        )

    def test_never_probed_is_stale(self):
        assert (
            _is_model_stale(
                status="online",
                last_checked_at=None,
                interval_seconds=60,
            )
            is True
        )

    def test_offline_model_does_not_become_stale(self):
        """A model we *know* is broken (offline / unauthorized /
        not_found) must not be re-labelled as 'stale' — that would
        hide a real problem behind a 'just slow' indicator.
        """
        for bad_status in ("offline", "unauthorized", "not_found", "rate_limited", "suspect"):
            assert (
                _is_model_stale(
                    status=bad_status,
                    last_checked_at=datetime.now(UTC) - timedelta(hours=1),
                    interval_seconds=60,
                )
                is False
            ), f"{bad_status} should not be re-labelled stale"

    def test_disabled_model_does_not_become_stale(self):
        """Disabled models are intentionally un-probed; calling
        them 'stale' would be confusing.
        """
        assert (
            _is_model_stale(
                status="disabled",
                last_checked_at=None,
                interval_seconds=60,
            )
            is False
        )

    def test_not_stale_when_within_max_effective_window(self):
        """A model probed 1h ago at the default 60s favorite interval
        is still within the max-effective window (60×40×2=4800s) and
        must NOT be stale. This is the core bug this fix addresses:
        the old threshold of 60×2=120s would falsely flag a model
        that is simply at 8× backoff + idle throttling.
        """
        assert (
            _is_model_stale(
                status="online",
                last_checked_at=datetime.now(UTC) - timedelta(seconds=3600),
                interval_seconds=60,
            )
            is False
        )
        """A regular model with 900s base interval: max effective is
        900 x 40 = 36000s, threshold = 72000s. 1700s is far below
        that, so still fresh. Catches the case where someone sets a
        long interval and the stale check fires too aggressively.
        """
        assert (
            _is_model_stale(
                status="online",
                last_checked_at=datetime.now(UTC) - timedelta(seconds=1700),
                interval_seconds=900,
            )
            is False
        )

    def test_stale_threshold_multiplier_is_two(self):
        """The stale threshold is documented as 2× the effective
        interval. Pinning this constant so a future tweak is
        intentional.
        """
        assert STALE_THRESHOLD_MULTIPLIER == 2
