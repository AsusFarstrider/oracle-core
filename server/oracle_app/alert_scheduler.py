from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import alerts as alerts_module
from . import audiobook_state
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .audiobook_runtime.playback import sync_then_control
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .memory.alerts import acknowledge_alert, claim_due_alerts


logger = logging.getLogger("oracle-brain.alerts")


def process_due_audiobook_sleep_timers(
    *,
    audiobook_execution: CanonicalAudiobookExecution,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime | None = None,
) -> int:
    clock = now or datetime.now(timezone.utc)
    completed = 0
    for source_id in sorted(satellites.enabled_satellite_ids_by_source):
        claimed = claim_due_alerts(
            source_id=source_id,
            now=clock,
            lease_seconds=30,
            limit=10,
            kind="sleep_timer",
            db_path=alerts_module.ALERT_DB_PATH,
        )
        for alert in claimed:
            status, result = sync_then_control(
                source=source_id,
                action="stop_longform_audio",
                close_session=True,
                require_sync_success=True,
                get_active_playback_for_source=(
                    audiobook_state.get_active_audiobook_playback_for_source
                ),
                execute_satellite_command=(
                    audiobook_execution.execute_satellite_command
                ),
                close_audiobook_session=audiobook_execution.close_session,
                sync_audiobook_session=audiobook_execution.sync_session,
                clear_active_playback=audiobook_state.clear_active_audiobook_playback,
            )
            if status != "executed":
                logger.error(
                    "audiobook_sleep_timer_stop_failed alert_id=%s source_id=%s error=%s",
                    alert.alert_id,
                    source_id,
                    str(result.get("error") or "unknown"),
                )
                continue
            acknowledge_alert(
                alert_id=alert.alert_id,
                source_id=source_id,
                lease_id=str(alert.lease_id),
                now=clock,
                completed=True,
                db_path=alerts_module.ALERT_DB_PATH,
            )
            completed += 1
    return completed


async def alert_scheduler_loop(
    *,
    audiobook_execution: CanonicalAudiobookExecution,
    satellites: SatelliteFleetRuntimeSettings,
    interval_seconds: float = 1.0,
) -> None:
    while True:
        try:
            process_due_audiobook_sleep_timers(
                audiobook_execution=audiobook_execution,
                satellites=satellites,
            )
        except Exception:
            logger.exception("alert_scheduler_iteration_failed")
        await asyncio.sleep(interval_seconds)
