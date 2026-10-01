"""
Background NIET Portal Sync Service

Fetches attendance and timetable data from NIET Portal
and saves it locally so Telegram commands can respond quickly.
"""

import asyncio
import logging

import assistant
import portal


logger = logging.getLogger("NIETSync")


# How often to sync with NIET Portal
SYNC_INTERVAL_SECONDS = 30 * 60  # 30 minutes


_sync_running = False


def sync_once():
    """
    Performs one complete NIET Portal sync.

    This runs in a worker thread because Playwright is blocking.
    """
    logger.info("Starting NIET Portal background sync...")

    try:
        # One login/session fetches attendance + timetable together
        full_data = portal.get_full_portal_data(fetch_weekly=True)

        if not full_data:
            logger.warning("NIET sync returned no data.")
            return False

        # Save attendance
        if full_data.get("attendance") is not None:
            assistant.save_json_file(
                assistant.ATTENDANCE_JSON_FILE,
                full_data["attendance"]
            )

        # Save today's schedule
        schedule_data = full_data.get("schedule", {})

        if schedule_data.get("today") is not None:
            assistant.save_json_file(
                assistant.TODAYS_SCHEDULE_JSON_FILE,
                schedule_data["today"]
            )

        # Save weekly schedule
        if schedule_data.get("weekly") is not None:
            assistant.save_json_file(
                assistant.WEEKLY_SCHEDULE_JSON_FILE,
                schedule_data["weekly"]
            )

        logger.info("NIET Portal background sync completed successfully.")
        return True

    except Exception as e:
        logger.error(
            f"NIET background sync failed: {type(e).__name__}: {e}"
        )
        return False


async def sync_loop():
    """
    Runs NIET synchronization continuously in the background.
    """
    global _sync_running

    if _sync_running:
        logger.info("NIET sync loop is already running.")
        return

    _sync_running = True

    logger.info(
        f"NIET background sync started. "
        f"Interval: {SYNC_INTERVAL_SECONDS // 60} minutes"
    )

    while True:
        try:
            # Run the blocking Playwright work outside the Telegram event loop
            await asyncio.to_thread(sync_once)

            # Wait before the next synchronization
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("NIET background sync cancelled.")
            break

        except Exception as e:
            logger.error(
                f"Unexpected NIET sync error: {type(e).__name__}: {e}"
            )

            # Don't kill the entire bot if one sync fails
            await asyncio.sleep(60)