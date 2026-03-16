# ios-device-recorder

CLI tool that captures a bug report from a connected iOS device in one shot — screen recording, screenshot, unified log stream, app sandbox files, filtered logs, crash logs, and a structured JSON report.

Compatible with macOS and Windows.

# Usage

Run main.py to start the recording.

Press **Ctrl+C** to stop the screen recording. The tool then captures remaining artifacts and writes everything to `ios-device-recordings/IOS_ISSUE_<timestamp>/`.

# Output structure

```
IOS_ISSUE_<timestamp>/
├── SYSTEM_OUTPUT/
│   ├── SCREEN_RECORDING_<ts>.mp4
│   ├── SCREENSHOT_<ts>.png
│   ├── LOG_STREAM_<ts>.log
│   ├── APP_LOGS_<ts>.log
│   ├── REPORT_<ts>.json
│   └── CRASH_LOGS/
└── SANDBOX_OUTPUT/            # app sandbox files
```

## Configuration

See [.env.example](.env.example) for all available environment variables.
