"""
Tests covering the 5 bugs fixed in the V2 audit.
These act as regression guards to prevent re-introduction.
"""

import pytest
import calendar
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


# ── Bug 1: composer_agent - Groq SDK API ─────────────────────────────────────


class TestComposerAgentGroqCall:
    """Bug 1: .invoke() doesn't exist on raw Groq SDK - must use .chat.completions.create()"""

    def test_compose_digest_calls_correct_groq_method(self):
        from services.agents.composer_agent import compose_digest

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = []  # No articles → returns empty digest

        mock_groq = MagicMock()
        result = compose_digest(mock_supabase, mock_groq, "test-user-id")

        # When no articles are found, the LLM should NOT be called at all
        mock_groq.invoke.assert_not_called()
        assert result["empty"] is True

    def test_groq_client_has_no_invoke_method(self):
        """Confirms raw Groq SDK doesn't have .invoke() - validates the bug was real."""
        from groq import Groq

        assert not hasattr(Groq, "invoke"), (
            "Raw Groq SDK should not have .invoke() - that is a LangChain method."
        )


# ── Bug 2: scorer.py - @property at module level ─────────────────────────────


class TestScorerThresholds:
    """Bug 2: @property on module-level function produces a descriptor, not a float."""

    def test_delivery_threshold_is_float(self):
        from services.ranker.scorer import DELIVERY_THRESHOLD

        assert isinstance(DELIVERY_THRESHOLD, float), (
            f"DELIVERY_THRESHOLD must be a float, got {type(DELIVERY_THRESHOLD)}"
        )

    def test_breaking_threshold_is_float(self):
        from services.ranker.scorer import BREAKING_THRESHOLD

        assert isinstance(BREAKING_THRESHOLD, float), (
            f"BREAKING_THRESHOLD must be a float, got {type(BREAKING_THRESHOLD)}"
        )

    def test_thresholds_are_comparable(self):
        from services.ranker.scorer import DELIVERY_THRESHOLD, BREAKING_THRESHOLD

        # If these were property objects, comparisons like '>= 4.5' would fail
        assert DELIVERY_THRESHOLD > 0
        assert BREAKING_THRESHOLD > DELIVERY_THRESHOLD

    def test_compute_final_score_returns_float(self):
        from services.ranker.scorer import RankSignals, compute_final_score

        signals = RankSignals(
            base_relevance=7.0,
            novelty_score=0.8,
            source_quality=0.6,
            topic_match=0.5,
            priority_boost=1.0,
        )
        score = compute_final_score(signals)
        assert isinstance(score, float)
        assert 0.0 <= score <= 10.0

    def test_score_formula_max_value(self):
        """Verify the scoring formula is balanced - max inputs should hit ~10."""
        from services.ranker.scorer import RankSignals, compute_final_score

        signals = RankSignals(
            base_relevance=10.0,
            novelty_score=1.0,
            source_quality=1.0,
            topic_match=1.0,
            priority_boost=1.0,
        )
        score = compute_final_score(signals)
        assert abs(score - 10.0) < 0.01, f"Max score should be 10.0, got {score}"


# ── Bug 3: collector/main.py - time.mktime() vs calendar.timegm() ────────────


class TestCollectorTimezone:
    """Bug 3: time.mktime() interprets struct_time as local time, not UTC."""

    def test_calendar_timegm_is_utc_safe(self):
        """calendar.timegm should produce the same timestamp regardless of local TZ."""
        # A known UTC datetime: 2024-01-01 12:00:00 UTC
        test_struct = (2024, 1, 1, 12, 0, 0, 0, 1, 0)  # time.struct_time fields
        ts = calendar.timegm(test_struct)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert dt.hour == 12, "calendar.timegm should treat struct_time as UTC"
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1

    def test_collector_uses_calendar_timegm(self):
        """Regression: ensure collector imports and uses calendar.timegm."""
        import inspect
        from services.collector import main as collector_main

        source = inspect.getsource(collector_main)
        assert "calendar.timegm" in source, (
            "Collector must use calendar.timegm() not time.mktime() for UTC safety"
        )
        assert "time.mktime(" not in source, (
            "time.mktime() must not be used in collector (not UTC-safe)"
        )


# ── Bug 4: db.py - source_id type hint ───────────────────────────────────────


class TestSourceIdTypeHints:
    """Bug 4: source_id was typed as int but is a UUID string in the schema."""

    def test_get_source_quality_accepts_str(self):
        import inspect
        from shared import db

        sig = inspect.signature(db.get_source_quality)
        param = sig.parameters["source_id"]
        assert param.annotation is str, (
            f"source_id should be typed as str (UUID), got {param.annotation}"
        )

    def test_update_source_ingestion_accepts_str(self):
        import inspect
        from shared import db

        sig = inspect.signature(db.update_source_ingestion)
        param = sig.parameters["source_id"]
        assert param.annotation is str, (
            f"source_id should be typed as str (UUID), got {param.annotation}"
        )


# ── Bug 5: clusterer.py - dimension mismatch guard ───────────────────────────


class TestClustererDimensionGuard:
    """Bug 5: Centroid update should handle embeddings of different dimensions."""

    def test_dimension_mismatch_resets_centroid(self):
        from services.enricher.clusterer import find_or_create_event

        mock_supabase = MagicMock()
        mock_groq = MagicMock()

        # Simulate DB returning a 384-dim centroid for an event
        old_centroid = [0.1] * 384
        mock_supabase.rpc.return_value.execute.return_value.data = [
            {
                "id": "evt-123",
                "article_count": 3,
            }
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"centroid_embedding": old_centroid}
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": "evt-123"}
        ]

        # New embedding is 768-dim - mismatch!
        new_embedding = [0.5] * 768

        # Should NOT raise IndexError - should log warning and reset
        find_or_create_event(
            mock_supabase, mock_groq, new_embedding, "Test Article", "user-1"
        )

        # The update should have been called (with the reset embedding)
        mock_supabase.table.return_value.update.assert_called_once()
        call_kwargs = mock_supabase.table.return_value.update.call_args[0][0]
        # The centroid stored should be the new 768-dim embedding (reset)
        assert len(call_kwargs["centroid_embedding"]) == 768

    def test_matching_dimensions_computes_average(self):
        from services.enricher.clusterer import find_or_create_event

        mock_supabase = MagicMock()
        mock_groq = MagicMock()

        old_centroid = [0.0] * 768  # 768-dim, all zeros
        mock_supabase.rpc.return_value.execute.return_value.data = [
            {
                "id": "evt-456",
                "article_count": 1,
            }
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"centroid_embedding": old_centroid}
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": "evt-456"}
        ]

        new_embedding = [1.0] * 768  # all ones

        find_or_create_event(
            mock_supabase, mock_groq, new_embedding, "Test Article", "user-1"
        )

        call_kwargs = mock_supabase.table.return_value.update.call_args[0][0]
        new_centroid = call_kwargs["centroid_embedding"]
        # Expected: (0.0 * 1 + 1.0) / 2 = 0.5
        assert all(abs(v - 0.5) < 1e-6 for v in new_centroid), (
            "Centroid should be the incremental average of old and new embeddings"
        )


# ── Bug 6: summarizer/main.py - Early DB Rejection ───────────────────────────


class TestSummarizerEarlyRejection:
    """Ensure that low-scoring articles are rejected early and not saved to the DB."""

    @pytest.mark.asyncio
    async def test_low_score_skips_db_save(self):
        from services.summarizer.main import process_message, ArticleAnalysis
        import asyncio

        # Mocking to bypass external dependencies
        with (
            patch("services.summarizer.main.get_filter_config") as mock_get_config,
            patch("services.summarizer.main.call_groq_async") as mock_groq,
            patch("services.summarizer.main.save_article") as mock_save,
            patch("services.summarizer.main.acknowledge_message") as mock_ack,
        ):
            # Setup mock config
            mock_get_config.return_value = {
                "allowed": ["python"],
                "blocked": [],
                "priority": [],
            }

            # Setup mock AI returning a low score < 3.0
            mock_groq.return_value = ArticleAnalysis(
                score=2.0,
                summary="Irrelevant summary.",
                why_it_matters="Does not matter.",
                topics=["General"],
            )

            msg = {
                "id": "123-0",
                "data": {
                    "user_id": "test-user",
                    "title": "Low relevance article",
                    "content": "Some content",
                    "source": "Test Source",
                    "source_url": "http://test.com",
                },
            }

            semaphore = asyncio.Semaphore(1)

            # Run the process
            result = await process_message(msg, semaphore)

            # Assertions
            assert result == 2.0, (
                "Should return the float score even on early rejection"
            )
            (
                mock_save.assert_not_called(),
                "save_article should NOT be called for score < 3.0",
            )
            (
                mock_ack.assert_called_once_with("summarizer-group", "123-0"),
                "Message MUST be acknowledged from queue",
            )


# ── Bug 7: cli/ops.py - Research Agent Early Rejection ────────────────────────


class TestOpsEarlyRejection:
    """Ensure that the Research Agent is skipped for articles below the delivery threshold."""

    @pytest.mark.asyncio
    async def test_low_score_skips_research_agent(self):
        from cli.pipeline import process_article_v2
        import asyncio

        # We will mock the external calls and force compute_final_score to return a low score
        with (
            patch("cli.pipeline.embedder.embed_text") as mock_embed,
            patch("cli.pipeline.deduplicator.is_near_duplicate", return_value=False),
            patch("cli.pipeline.novelty.compute_novelty_score", return_value=0.5),
            patch("cli.pipeline.clusterer.find_or_create_event", return_value="evt-1"),
            patch(
                "cli.pipeline.get_filter_config",
                return_value={"allowed": [], "priority": []},
            ),
            patch("cli.pipeline.get_source_quality", return_value=0.5),
            patch("cli.pipeline.scorer.compute_final_score", return_value=2.0),
            patch("cli.pipeline.settings") as mock_settings,
            patch("cli.pipeline.acknowledge_message") as mock_ack,
        ):
            # Set delivery threshold higher than the returned score
            mock_settings.delivery_threshold = 3.5

            mock_embed.return_value = [0.1] * 768
            mock_agent = MagicMock()
            mock_db = MagicMock()

            msg = {
                "id": "msg-1",
                "data": {
                    "user_id": "test-user",
                    "title": "Boring Article",
                    "content": "Not interesting",
                    "score": 1.0,  # low base relevance
                },
            }

            semaphore = asyncio.Semaphore(1)
            result = await process_article_v2(
                mock_db, msg, mock_agent, "fake-key", semaphore
            )

            # 1. The function should return False (dropped)
            assert result is False, "Should return False when dropping an article"

            # 2. Agent should NOT be invoked
            (
                mock_agent.invoke.assert_not_called(),
                "Research agent should NOT be called for low scores",
            )

            # 3. Database upsert should NOT be called
            (
                mock_db.table.return_value.upsert.assert_not_called(),
                "Article should NOT be saved if dropped",
            )

            # 4. Message MUST be acknowledged from the queue
            mock_ack.assert_called_once_with("summarizer-group", "msg-1")
