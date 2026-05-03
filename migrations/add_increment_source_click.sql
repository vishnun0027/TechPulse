-- Migration: Add increment_source_click RPC
-- Run this in your Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- This enables the feedback loop: when a user clicks an article link in the
-- dashboard, it increments articles_clicked in source_health, which feeds
-- into the quality_score used by the AI ranker.

CREATE OR REPLACE FUNCTION increment_source_click(p_source_id bigint, p_user_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    -- Upsert: create the row if it doesn't exist, else increment the click counter
    INSERT INTO source_health (source_id, user_id, articles_clicked, articles_delivered, quality_score)
    VALUES (p_source_id, p_user_id, 1, 0, 0.5)
    ON CONFLICT (source_id, user_id)
    DO UPDATE SET
        articles_clicked = source_health.articles_clicked + 1,
        -- Recompute quality_score = clicked / delivered (capped between 0.1 and 1.0)
        quality_score = GREATEST(
            0.1,
            LEAST(
                1.0,
                CASE
                    WHEN (source_health.articles_delivered + EXCLUDED.articles_delivered) > 0
                    THEN ROUND(
                        (source_health.articles_clicked + 1)::numeric /
                        GREATEST(source_health.articles_delivered, 1),
                        4
                    )
                    ELSE 0.5
                END
            )
        ),
        updated_at = now();
END;
$$;
