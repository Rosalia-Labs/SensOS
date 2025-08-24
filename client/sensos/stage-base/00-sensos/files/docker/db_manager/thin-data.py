# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

def get_richest_week(conn):
    """
    Returns the start date of the week (as a datetime) with the most non-zeroed segments,
    using the calculated segment timestamp.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                date_trunc(
                    'week',
                    af.capture_timestamp + (ag.start_frame * INTERVAL '1 second') / af.sample_rate
                ) AS week_start,
                COUNT(*) AS num_segments
            FROM sensos.audio_segments ag
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE NOT ag.zeroed
            GROUP BY week_start
            ORDER BY num_segments DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row["week_start"]:
            logger.info(
                f"Richest week starts {row['week_start']} with {row['num_segments']} unzeroed segments."
            )
            return row["week_start"]
        else:
            logger.info("No non-zeroed segments found in any week.")
            return None


def get_lowest_score_segment_for_frequent_label(conn, week_start):
    """
    Finds the segment in the given week whose top BirdNET label is the most frequent label,
    and whose top label's score is the lowest among such segments.
    Returns the segment info and the top label/score.
    """
    from datetime import timedelta

    week_end = week_start + timedelta(weeks=1)

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH week_segments AS (
                SELECT id
                FROM sensos.audio_segments
                WHERE NOT zeroed
                AND segment_start_time >= %s
                AND segment_start_time < %s
            ),
            top_scores AS (
                SELECT bs.segment_id, bs.label, bs.score
                FROM sensos.birdnet_scores bs
                INNER JOIN (
                    SELECT segment_id, MAX(score) AS max_score
                    FROM sensos.birdnet_scores
                    GROUP BY segment_id
                ) ms ON bs.segment_id = ms.segment_id AND bs.score = ms.max_score
                WHERE bs.segment_id IN (SELECT id FROM week_segments)
            ),
            most_freq_label AS (
                SELECT label
                FROM top_scores
                GROUP BY label
                ORDER BY COUNT(*) DESC
                LIMIT 1
            )
            SELECT ts.segment_id, ts.label, ts.score,
                af.file_path, af.id AS file_id,
                ag.channel, ag.start_frame, ag.end_frame
            FROM top_scores ts
            JOIN sensos.audio_segments ag ON ts.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE ts.label = (SELECT label FROM most_freq_label)
            AND af.file_path LIKE '%%.wav'
            ORDER BY ts.score ASC
            LIMIT 1
            """,
            (week_start, week_end),
        )
        row = cur.fetchone()
        if row:
            logger.info(
                f"Lowest score segment with most frequent label '{row['label']}' in week {week_start} "
                f"has score {row['score']} (segment id {row['segment_id']})."
            )
            return row
        else:
            logger.info(
                f"No segments found for most frequent label in week starting {week_start}"
            )
            return None


def zero_redundant_segments(conn, min_free_gb=32):
    while True:
        disk = get_disk_free_gb_and_percent(AUDIO_BASE)
        if disk is not None and disk["disk_available_gb"] > min_free_gb:
            logger.info("Enough disk space. Done.")
            break
        elif disk is None:
            logger.warning("Could not determine disk space. Skipping cleanup for now.")
            break

        week = get_richest_week(conn)
        if not week:
            logger.info("No more weeks to clean.")
            break

        seg = get_lowest_score_segment_for_frequent_label(conn, week)
        if not seg:
            logger.info(f"No segment to zero out in week {week}.")
            break

        logger.info(
            f"Zeroing segment {seg['segment_id']} (label='{seg['label']}', score={seg['score']}) "
            f"in week {week}."
        )
        zero_segment(conn, seg)

        logger.info("Pass complete. Checking disk again.")
