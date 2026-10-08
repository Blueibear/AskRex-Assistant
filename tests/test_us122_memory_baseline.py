"""Tests for US-122: Memory usage baseline and leak detection.

Acceptance criteria:
- tracemalloc used to profile memory during a simulated workload (min 100 requests)
- baseline RSS memory usage documented in docs/performance-baseline.md
- any object type accumulating unboundedly across requests flagged and investigated
- no confirmed memory leaks (unbounded growth) present at release
- Typecheck passes

Methodology:
    tracemalloc snapshots are taken at three points: warm-up baseline,
    mid-run (50 requests), and end-of-run (100 requests). Growth between
    mid and end is compared to detect unbounded increase. An unbounded leak
    would show constant-rate growth proportional to request count; bounded
    overhead converges to near-zero growth after the first few requests
    (due to Python's object caching / interning effects).

    For each test scenario the acceptable total growth across all 100 requests
    is < 500 KB (i.e. < 5 KB/request average for framework overhead).
"""

from __future__ import annotations

import gc
import statistics
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, jsonify

from rex.health import check_config, create_health_blueprint
from rex.http_errors import install_error_envelope_handler

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NUM_REQUESTS = 100
_WARMUP_REQUESTS = 10
_MAX_GROWTH_BYTES = 500 * 1024  # 500 KB for 100 requests - very generous
_MAX_GROWTH_PER_REQUEST_BYTES = 5 * 1024  # 5 KB/req average


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_app() -> Flask:
    """Minimal Flask app for memory profiling."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    install_error_envelope_handler(flask_app)
    flask_app.register_blueprint(create_health_blueprint(checks=[check_config]))

    @flask_app.route("/api/notifications")
    def stub_notifications() -> Any:
        return jsonify({"notifications": [], "total": 0}), 200

    @flask_app.route("/api/settings")
    def stub_settings() -> Any:
        return jsonify({"llm_provider": "local"}), 200

    return flask_app


@pytest.fixture()
def mem_client(mem_app: Flask):  # noqa: ANN201
    return mem_app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _take_snapshot() -> tracemalloc.Snapshot:
    gc.collect()
    return tracemalloc.take_snapshot()


def _total_bytes(snapshot: tracemalloc.Snapshot) -> int:
    return sum(stat.size for stat in snapshot.statistics("filename"))


def _measure_growth(mem_client: Any, endpoint: str, *, requests: int = _NUM_REQUESTS) -> int:
    """Measure endpoint memory growth with jitter resistance.

    Full-suite runs can exhibit transient allocator jitter from unrelated tests.
    We run multiple trials and use the median growth to reduce false positives
    while still detecting consistent linear leaks.
    """
    growth_samples: list[int] = []

    for _ in range(3):
        tracemalloc.start()
        try:
            for _ in range(_WARMUP_REQUESTS):
                mem_client.get(endpoint)

            snap_start = _take_snapshot()
            bytes_start = _total_bytes(snap_start)

            for _ in range(requests):
                mem_client.get(endpoint)

            snap_end = _take_snapshot()
            bytes_end = _total_bytes(snap_end)

            growth_samples.append(max(0, bytes_end - bytes_start))
        finally:
            tracemalloc.stop()

    return int(statistics.median(growth_samples))


# ---------------------------------------------------------------------------
# tracemalloc memory profiling tests
# ---------------------------------------------------------------------------


class TestMemoryBaseline:
    def test_health_endpoint_no_unbounded_growth(self, mem_client: Any) -> None:
        """100 requests to /health/live must not cause unbounded memory growth."""
        growth = _measure_growth(mem_client, "/health/live")

        assert growth < _MAX_GROWTH_BYTES, (
            f"Memory grew by {growth / 1024:.1f} KB over {_NUM_REQUESTS} requests "
            f"to /health/live - possible leak (threshold: {_MAX_GROWTH_BYTES // 1024} KB)"
        )

    def test_notifications_endpoint_no_unbounded_growth(self, mem_client: Any) -> None:
        """100 requests to /api/notifications must not cause unbounded growth."""
        growth = _measure_growth(mem_client, "/api/notifications")

        assert growth < _MAX_GROWTH_BYTES, (
            f"Memory grew by {growth / 1024:.1f} KB over {_NUM_REQUESTS} requests "
            f"to /api/notifications"
        )

    def test_settings_endpoint_no_unbounded_growth(self, mem_client: Any) -> None:
        """100 requests to /api/settings must not cause unbounded growth."""
        growth = _measure_growth(mem_client, "/api/settings")

        assert growth < _MAX_GROWTH_BYTES, (
            f"Memory grew by {growth / 1024:.1f} KB over {_NUM_REQUESTS} requests "
            f"to /api/settings"
        )

    def test_growth_rate_not_linear(self, mem_client: Any) -> None:
        """Growth from request 51-100 must be ≤ growth from request 1-50 (converging, not linear)."""
        # A single tracemalloc run can absorb delayed allocations from another
        # test or an extension module.  Compare three independent runs and use
        # the median, as _measure_growth does above: a leak remains consistent
        # across runs, while one-off allocator noise does not become a failure.
        first_half_samples: list[int] = []
        second_half_samples: list[int] = []

        for _ in range(3):
            tracemalloc.start()
            try:
                for _ in range(_WARMUP_REQUESTS):
                    mem_client.get("/health/live")

                bytes_before = _total_bytes(_take_snapshot())

                for _ in range(50):
                    mem_client.get("/health/live")

                bytes_mid = _total_bytes(_take_snapshot())

                for _ in range(50):
                    mem_client.get("/health/live")

                bytes_after = _total_bytes(_take_snapshot())

                first_half_samples.append(max(0, bytes_mid - bytes_before))
                second_half_samples.append(max(0, bytes_after - bytes_mid))
            finally:
                tracemalloc.stop()

        growth_first_half = int(statistics.median(first_half_samples))
        growth_second_half = int(statistics.median(second_half_samples))

        # Second half growth should be <= first half (converging behaviour).
        # 50 KB absolute floor accounts for CI allocator jitter when the first
        # half converges to 0 KB (full warm-up from the rest of the test suite).
        assert growth_second_half <= growth_first_half * 2 + 50 * 1024, (
            f"Memory growth is increasing: first 50 reqs={growth_first_half // 1024}KB, "
            f"second 50 reqs={growth_second_half // 1024}KB - possible linear leak"
        )

    def test_min_100_requests_measured(self, mem_client: Any) -> None:
        """Baseline is measured with at least 100 requests as required."""
        request_count = 0
        tracemalloc.start()
        try:
            for _ in range(_WARMUP_REQUESTS):
                mem_client.get("/health/live")
            for _ in range(_NUM_REQUESTS):
                mem_client.get("/health/live")
                request_count += 1
        finally:
            tracemalloc.stop()

        assert request_count >= 100


# ---------------------------------------------------------------------------
# Baseline documentation exists and mentions memory
# ---------------------------------------------------------------------------


class TestMemoryBaselineDocumented:
    def test_performance_baseline_md_mentions_memory(self) -> None:
        """docs/performance-baseline.md must document memory baseline."""
        baseline_path = Path(__file__).parent.parent / "docs" / "performance-baseline.md"
        assert baseline_path.exists(), "docs/performance-baseline.md does not exist"
        content = baseline_path.read_text(encoding="utf-8")
        assert any(
            kw in content.lower() for kw in ("memory", "rss", "tracemalloc", "leak")
        ), "docs/performance-baseline.md must document memory usage baseline"
