#!/usr/bin/env python3

import logging
import os
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path

load_dotenv()

from utils import ios


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

APP_BUNDLE_ID = os.environ["APP_BUNDLE_ID"]


def main() -> None:
    udid = os.environ.get("UDID") or ios.get_first_udid()
    if not udid:
        logger.error("No UDID provided and no device found.")
        return

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d-%H-%M-%S")

    # Define directories
    base_dir = Path(__file__).parent / "ios-device-recordings" / f"IOS_ISSUE_{date_str}"
    base_dir.mkdir(parents=True, exist_ok=True)
    system_dir = base_dir / "SYSTEM_OUTPUT"
    system_dir.mkdir(exist_ok=True)
    crash_logs_path = system_dir / "CRASH_LOGS"
    crash_logs_path.mkdir(exist_ok=True)

    # Artifact paths/names
    video_path = system_dir / f"SCREEN_RECORDING_{date_str}.mp4"
    img_path = system_dir / f"SCREENSHOT_{date_str}.png"
    log_path = system_dir / f"APP_LOGS_{date_str}.log"
    report_path = system_dir / f"REPORT_{date_str}.json"

    # Tunneld + device
    proc = ios.ensure_tunneld()
    if proc:
        logger.info("tunneld started (pid: %d)", proc.pid)
    else:
        logger.info("tunneld already running.")

    device_info = ios.get_device_info(udid)
    if not device_info:
        logger.error("Device not found.")
        return

    # Record
    ios.record_video(video_path)

    # Screenshot
    ios.take_screenshot(img_path)
    logger.info("Screenshot saved.")

    # App data from sandbox
    sandbox_dir = base_dir / "SANDBOX_OUTPUT"
    sandbox_dir.mkdir(exist_ok=True)
    logger.info("Pulling app sandbox data from device...")
    try:
        asyncio.run(ios.pull_app_sandbox(udid, APP_BUNDLE_ID, sandbox_dir))
        logger.info("App data pulled.")
    except Exception as e:
        logger.warning("App data pull failed: %s", e)

    # Logs
    logs_dir = sandbox_dir / "Library_Caches_Logs"
    log_file = ios.find_log_file(logs_dir, APP_BUNDLE_ID) if logs_dir.exists() else None
    if log_file:
        logger.info("Log file found: %s", log_file.name)
        start_line = ios.filter_logs_by_date(log_file, now, log_path)
        logger.info("Filtered log file created: %s", log_path.name)
        logger.debug("Search date: %s", now.strftime(ios.LOG_DATE_FORMAT))
        logger.debug("Start line: %d", start_line)
    else:
        logger.warning("No log file containing app bundle ID found.")
        log_path.write_text("")

    # Form + details
    app_info = ios.get_app_info(udid, APP_BUNDLE_ID)
    extracted_value = ios.extract_log_value(log_file)
    ios.write_report(report_path, device_info, app_info, extracted_value)
    logger.info("Report written.")

    # Crash logs
    ios.pull_crash_logs(udid, crash_logs_path)
    logger.info("Crash logs pulled.")

    logger.info("All done. Output: %s", base_dir)


if __name__ == "__main__":
    main()
