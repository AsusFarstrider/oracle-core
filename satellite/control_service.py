from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from oracle_runtime_config import (
    build_config_report_payload,
    choose_config_report_format,
    log_config_findings,
    render_config_report_text,
)
from satellite.control_service_runtime import (
    CommandResult,
    ControlRequestHandler,
    ControlServer,
    LocalPlaybackAdapter,
    ShellPlexampAdapter,
)
from satellite.control_service_runtime.config_runtime import build_control_service_runtime_report
from satellite.control_service_runtime.settings import ControlServiceSettings


logger = logging.getLogger("oracle-satellite-control")


def main() -> None:
    from satellite.control_service_runtime.startup import resolve_control_service_settings

    args = resolve_control_service_settings()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))
    report_sections = [("Satellite control service config check:", build_control_service_runtime_report(args))]
    findings = report_sections[0][1]
    log_config_findings(findings, logger_name="oracle-satellite-control.config")
    if any(str(item.get("severity") or "").lower() == "error" for item in findings):
        raise SystemExit(1)
    if args.adapter == "shell":
        adapter = ShellPlexampAdapter(args)
    else:
        adapter = LocalPlaybackAdapter(args)
    server = ControlServer(
        (args.bind_host, args.bind_port),
        ControlRequestHandler,
        api_key=args.api_key,
        adapter=adapter,
        reply_audio_state_path=args.reply_audio_state_path,
        reply_audio_stop_path=args.reply_audio_stop_path,
        result_type=CommandResult,
        build_config_report_payload=lambda: build_config_report_payload(
            service="oracle-satellite-control",
            report_sections=[("Satellite control service config check:", build_control_service_runtime_report(args))],
        ),
        render_config_report_text=lambda: render_config_report_text(
            [("Satellite control service config check:", build_control_service_runtime_report(args))]
        ),
        choose_config_report_format=choose_config_report_format,
    )
    logger.info("Starting satellite control service on %s:%s", args.bind_host, args.bind_port)
    server.serve_forever()

if __name__ == "__main__":
    main()
