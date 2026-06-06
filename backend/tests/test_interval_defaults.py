"""Tests for the default probe-interval constants.

These constants encode a product decision, not a technical one:
  - Favorite models are the ones the user actively cares about, so
    they must be probed MORE often (faster failure detection).
  - Regular models are barely used, so probing them more often than
    the user looks at them is wasted upstream traffic.

The test pins both directions (favorite < regular) AND a concrete
range, so a future "let's just bump them both up" refactor has to
think about the asymmetry explicitly.
"""

from __future__ import annotations


def test_favorite_interval_is_shorter_than_regular():
    """Favorite (focus) models must be checked more often than
    regular (rarely-used) models — the user marked favorites
    specifically to get fresher signal on them.
    """
    from app.services.settings import (
        DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS,
        DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS,
    )

    assert DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS < DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS, (
        f"favorite ({DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS}s) must be "
        f"shorter than regular ({DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS}s)"
    )


def test_favorite_interval_is_at_most_one_minute():
    """A 'focus' model that takes 5+ minutes to surface a failure is
    not a focus — the user wants to know quickly. Upper bound: 60s.
    """
    from app.services.settings import DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS

    assert DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS <= 60


def test_regular_interval_is_at_least_ten_minutes():
    """For a model the user barely uses, polling every 2 minutes is
    wasted upstream traffic. Lower bound: 10 minutes (600s).
    """
    from app.services.settings import DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS

    assert DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS >= 600


def test_random_sweep_is_a_supplement_not_a_replacement():
    """The random sweep job is meant to spread load and look like
    human traffic, not to re-probe every model faster than its
    natural schedule. It must fire less often than the most
    frequent model interval, otherwise it undoes the regular
    interval for any model it picks.
    """
    from app.core.scheduler import RANDOM_SWEEP_INTERVAL_SECONDS
    from app.services.settings import DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS

    assert RANDOM_SWEEP_INTERVAL_SECONDS >= DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS, (
        f"random sweep ({RANDOM_SWEEP_INTERVAL_SECONDS}s) must be >= "
        f"favorite interval ({DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS}s); "
        f"otherwise it would re-probe focus models faster than their "
        f"natural schedule."
    )


def test_default_timeout_is_at_least_sixty_seconds():
    """A 30s timeout was too aggressive for streaming models that
    legitimately take 10-40s to produce the first token (TTFB).
    With a 30s timeout, 80% of probes against the 咸鱼-MiniMax proxy
    timed out (3764/4678 in 35h), most of which would have succeeded
    with a 60s timeout. The upper bound in settings validation is
    600s, so 60s is still conservative.
    """
    from app.core.config import get_settings

    assert get_settings().default_timeout_seconds == 60, (
        f"expected 60s default, got {get_settings().default_timeout_seconds}s"
    )


def test_default_rate_limit_does_not_starve_normal_load():
    """A typical deployment (N models split across providers) plus
    random-sweep + favorites must not constantly hit the
    provider-level rate limit on the default config — otherwise the
    rate limit becomes a silent source of skipped probes.

    Headroom math: assume 2 providers, 50 models (5 fav + 45 reg),
    and the *worst* case (no adaptive backoff, no idle throttle,
    nobody watching). Default rate limit of 20/min/provider should
    still let a single provider's worth of probes through.
    """
    from app.core.scheduler import (
        RANDOM_SWEEP_INTERVAL_SECONDS,
        RANDOM_SWEEP_PROBES_PER_TICK,
    )
    from app.services.settings import (
        DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS,
        DEFAULT_PROVIDER_RATE_LIMIT_PER_MINUTE,
        DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS,
    )

    # Models per provider in a 2-provider / 50-model deployment.
    models_per_provider = 25
    fav_per_provider = 3  # 5 favs split roughly evenly
    reg_per_provider = models_per_provider - fav_per_provider

    # Probes per minute per provider from natural schedule alone.
    natural_probes_per_min = fav_per_provider * (
        60 / DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS
    ) + reg_per_provider * (60 / DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS)
    # Plus contribution from random sweep (proportional to models).
    sweep_probes_per_min = (
        RANDOM_SWEEP_PROBES_PER_TICK * (60 / RANDOM_SWEEP_INTERVAL_SECONDS) * (models_per_provider / 50)
    )

    worst_case = natural_probes_per_min + sweep_probes_per_min
    # Allow 10% headroom — the limit is per-minute so a single
    # burst shouldn't starve the schedule.
    assert worst_case < DEFAULT_PROVIDER_RATE_LIMIT_PER_MINUTE, (
        f"worst-case {worst_case:.1f} probes/min/provider must be "
        f"< rate limit {DEFAULT_PROVIDER_RATE_LIMIT_PER_MINUTE}/min"
    )
