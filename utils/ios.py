"""iOS device utilities — stateless functions for interacting with a connected iOS device."""

import json
import logging
import os
import platform
import plistlib
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEVICE_NAMES: dict[str, str] = {
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
}

LOG_DATE_REGEX = os.environ["LOG_DATE_REGEX"]
LOG_DATE_FORMAT = os.environ["LOG_DATE_FORMAT"]
LOG_EXTRACT_REGEX = os.environ["LOG_EXTRACT_REGEX"]
LOG_EXTRACT_EXCLUDE = os.environ["LOG_EXTRACT_EXCLUDE"]

FORM_VAR_1 = os.environ.get("FORM_VAR_1", "")
FORM_VAR_3 = os.environ.get("FORM_VAR_3", "")
FORM_VAR_4 = os.environ.get("FORM_VAR_4", "")
FORM_VAR_5 = os.environ.get("FORM_VAR_5", "")
FORM_VAR_6 = os.environ.get("FORM_VAR_6", "")

SANDBOX_DIRS = [
    "/Library/Caches/Logs/",
    "/Library/Preferences/",
    "/Library/Application Support/",
    "/Library/Cookies/",
    "/Library/WebKit/",
    "/Documents/",
    "/tmp/",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result. Logs stderr on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0 and result.stderr:
        logger.warning(result.stderr.strip())
    return result


def get_friendly_device_name(product_type: str) -> str:
    """Map a ProductType string (e.g. ``iPhone15,4``) to a friendly name."""
    return DEVICE_NAMES.get(product_type, product_type)


# ---------------------------------------------------------------------------
# Tunnel management
# ---------------------------------------------------------------------------

def _is_tunneld_running() -> bool:
    """Check whether pymobiledevice3 tunneld is already running (cross-platform)."""
    if platform.system() == "Windows":
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True, text=True,
        )
        return "pymobiledevice3" in result.stdout or "tunneld" in result.stdout
    else:
        result = subprocess.run(
            ["pgrep", "-f", "pymobiledevice3 remote tunneld"],
            capture_output=True, text=True,
        )
        return result.returncode == 0


def ensure_tunneld() -> Optional[subprocess.Popen[bytes]]:
    """Start tunneld if not already running. Returns the Popen object, or None if already running."""
    if _is_tunneld_running():
        return None

    if platform.system() == "Windows":
        cmd = [sys.executable, "-m", "pymobiledevice3", "remote", "tunneld"]
    else:
        cmd = ["sudo", sys.executable, "-m", "pymobiledevice3", "remote", "tunneld"]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    return proc


# ---------------------------------------------------------------------------
# Device / app info
# ---------------------------------------------------------------------------

def get_device_info(udid: Optional[str] = None) -> dict[str, Any]:
    """Return device info for *udid*, or the first connected device if omitted. Empty dict if not found."""
    result = run_cmd([sys.executable, "-m", "pymobiledevice3", "usbmux", "list"])
    try:
        devices = json.loads(result.stdout)
        if udid:
            for d in devices:
                if d.get("Identifier") == udid or d.get("UniqueDeviceID") == udid:
                    return d
        if devices:
            return devices[0]
    except (json.JSONDecodeError, IndexError):
        pass
    return {}


def get_first_udid() -> Optional[str]:
    """Return the UDID of the first connected iOS device, or None."""
    info = get_device_info()
    return info.get("Identifier") or info.get("UniqueDeviceID")


def get_app_info(udid: str, bundle_id: str) -> dict[str, Any]:
    """Return installed app info for *bundle_id* on *udid*. Empty dict if not found."""
    result = run_cmd([
        sys.executable, "-m", "pymobiledevice3", "apps", "list",
        "--udid", udid, "-t", "User",
    ])
    try:
        apps = json.loads(result.stdout)
        return apps.get(bundle_id, {})
    except (json.JSONDecodeError, AttributeError):
        pass
    return {}


# ---------------------------------------------------------------------------
# Screenshot / screen recording
# ---------------------------------------------------------------------------

def take_screenshot(img_path: Path) -> None:
    """Take a screenshot via pymobiledevice3 CLI."""
    run_cmd([sys.executable, "-m", "pymobiledevice3", "developer", "dvt", "screenshot", str(img_path)])


async def capture_frames(
    on_frame: Callable[[bytes], None],
    stop_check: Callable[[], bool],
    num_channels: int = 4,
) -> int:
    """Capture frames via *num_channels* parallel DVT screenshot channels. Returns total frame count."""
    import asyncio

    from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS, get_tunneld_devices
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.screenshot import ScreenshotService

    rsds = await get_tunneld_devices(TUNNELD_DEFAULT_ADDRESS)
    if not rsds:
        logger.info("Waiting for tunnel device to appear...")
        for _ in range(10):
            await asyncio.sleep(2)
            rsds = await get_tunneld_devices(TUNNELD_DEFAULT_ADDRESS)
            if rsds:
                break
    if not rsds:
        logger.warning("No tunnel devices found. Is tunneld running?")
        return 0

    total_frames = 0
    lock = asyncio.Lock()

    async def _channel_loop(svc: ScreenshotService) -> int:
        nonlocal total_frames
        count = 0
        while not stop_check():
            try:
                frame_data = await svc.take_screenshot()
                async with lock:
                    on_frame(frame_data)
                    total_frames += 1
                count += 1
            except Exception as e:
                if not stop_check():
                    logger.error(f"Frame capture error: {e}")
                break
        return count

    try:
        async with DvtProvider(rsds[0]) as dvt:
            channels = []
            for _ in range(num_channels):
                svc = await dvt.dtx.open_channel(ScreenshotService)
                channels.append(svc)
            tasks = [asyncio.create_task(_channel_loop(svc)) for svc in channels]
            while not stop_check():
                await asyncio.sleep(0.1)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for r in rsds:
            await r.close()

    return total_frames


def record_video(video_path: Path, fps: int = 30) -> None:
    """Capture device screen frames via DVT, pipe to ffmpeg as MP4."""
    import asyncio
    import signal

    stop_flag = False

    ffmpeg_proc = subprocess.Popen([
        "ffmpeg", "-y",
        "-use_wallclock_as_timestamps", "1",
        "-f", "image2pipe", "-i", "-",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(video_path),
    ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def on_frame(data: bytes) -> None:
        if ffmpeg_proc.stdin:
            ffmpeg_proc.stdin.write(data)

    def stop_check() -> bool:
        return stop_flag

    def _stop_handler(signum: int, _frame: Optional[FrameType]) -> None:
        nonlocal stop_flag
        stop_flag = True
        logger.info("Recording stopped.")

    original_handler = signal.signal(signal.SIGINT, _stop_handler)

    logger.info("Starting recording... (press Ctrl+C to stop)")
    loop = asyncio.new_event_loop()
    try:
        frame_count = loop.run_until_complete(capture_frames(on_frame, stop_check))
    finally:
        loop.close()

    signal.signal(signal.SIGINT, original_handler)

    if ffmpeg_proc.stdin:
        ffmpeg_proc.stdin.close()
    ffmpeg_proc.wait()

    if frame_count == 0:
        logger.warning("No frames captured.")
    elif ffmpeg_proc.returncode != 0:
        err = ffmpeg_proc.stderr.read().decode() if ffmpeg_proc.stderr else "unknown error"
        logger.warning("ffmpeg failed: %s", err)
    else:
        logger.info("Captured %d frames.", frame_count)
        logger.info("Recording complete.")


# ---------------------------------------------------------------------------
# App sandbox / file operations
# ---------------------------------------------------------------------------

async def pull_app_sandbox(
    udid: str,
    bundle_id: str,
    issue_dir: Path,
    sandbox_dirs: Optional[list[str]] = None,
) -> None:
    """Pull app sandbox directories via HouseArrestService (AFC) and convert binary plists to XML."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.house_arrest import HouseArrestService

    if sandbox_dirs is None:
        sandbox_dirs = SANDBOX_DIRS

    lockdown = await create_using_usbmux(serial=udid)
    service = await HouseArrestService.create(lockdown=lockdown, bundle_id=bundle_id)

    for remote_dir in sandbox_dirs:
        dir_name = remote_dir.strip("/").replace("/", "_")
        local_dir = issue_dir / dir_name
        try:
            files = await service.listdir(remote_dir)
        except Exception:
            continue

        pulled = 0
        for fname in files:
            if fname in (".", ".."):
                continue
            remote_path = remote_dir + fname
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / fname
            try:
                await service.pull(remote_path, str(local_path))
                pulled += 1
            except Exception as e:
                logger.warning(f"Could not pull {remote_path}: {e}")

        if pulled:
            logger.info("%s: %d file(s)", dir_name, pulled)

    convert_plists_to_xml(issue_dir)


def convert_plists_to_xml(directory: Path) -> None:
    """Convert binary plists under *directory* to XML for readability."""
    for plist_file in directory.rglob("*.plist"):
        try:
            data = plistlib.loads(plist_file.read_bytes(), fmt=plistlib.FMT_BINARY)
            plist_file.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
        except Exception:
            pass


def pull_crash_logs(udid: str, system_logs_path: Path) -> None:
    """Pull crash logs for the current month from *udid* into *system_logs_path*."""
    today_pattern = datetime.now().strftime("%Y-%m") + r"(-[0-9]{2})?"
    run_cmd([
        sys.executable, "-m", "pymobiledevice3", "crash", "pull",
        "--udid", udid, "--match", today_pattern, str(system_logs_path),
    ])


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def find_log_file(directory: Path, bundle_id: str) -> Optional[Path]:
    """Return the first file in *directory* whose contents contain *bundle_id*, or None."""
    for f in directory.iterdir():
        if f.is_file() and f.suffix not in (".mp4", ".png"):
            try:
                if bundle_id in f.read_text(errors="ignore"):
                    return f
            except Exception:
                continue
    return None


def filter_logs_by_date(log_file: Path, target_dt: datetime, output_path: Path) -> int:
    """Write lines from *log_file* at or after *target_dt* to *output_path*. Returns start line index."""
    timestamp_re = re.compile(LOG_DATE_REGEX)

    lines = log_file.read_text(errors="ignore").splitlines(keepends=True)
    if not lines:
        output_path.write_text("")
        return 0

    target_str = target_dt.strftime(LOG_DATE_FORMAT)

    timestamps = []
    for line in lines:
        m = timestamp_re.match(line)
        timestamps.append(m.group(1) if m else "")

    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        if timestamps[mid] and timestamps[mid] >= target_str:
            hi = mid
        else:
            lo = mid + 1

    output_path.write_text("".join(lines[lo:]))
    return lo


def extract_log_value(log_file: Optional[Path]) -> str:
    """Return the last non-excluded LOG_EXTRACT_REGEX match in *log_file*, or "unknown"."""
    if not log_file or not log_file.exists():
        return "unknown"
    content = log_file.read_text(errors="ignore")
    matches = re.findall(LOG_EXTRACT_REGEX, content)
    for value in reversed(matches):
        if value != LOG_EXTRACT_EXCLUDE:
            return value
    return "unknown"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def write_report(
    report_path: Path,
    device_info: dict[str, Any],
    app_info: dict[str, Any],
    extracted_value: str,
) -> None:
    """Write a combined JSON report with form fields and full device details to *report_path*."""
    model = get_friendly_device_name(
        device_info.get("ProductType", "unknown")
    )
    os_version = device_info.get("ProductVersion", "unknown")
    build = device_info.get("BuildVersion", "unknown")
    app_version = app_info.get("CFBundleShortVersionString", "unknown")
    app_build = app_info.get("CFBundleVersion", "unknown")

    report = {
        "form": {
            "variable_name_1": FORM_VAR_1,
            "variable_name_2": extracted_value,
            "app_version": app_version,
            "app_build": app_build,
            "mobile_device": model,
            "os_version": f"{os_version} ({build})",
            "variable_name_3": FORM_VAR_3,
            "variable_name_4": FORM_VAR_4,
            "variable_name_5": FORM_VAR_5,
            "variable_name_6": FORM_VAR_6,
        },
        "device_details": device_info,
    }
    report_path.write_text(json.dumps(report, indent=2))
