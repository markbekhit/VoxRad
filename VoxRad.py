import argparse
import sys


def _run_desktop():
    from ui.main_window import initialize_ui
    initialize_ui()


def _run_web(host: str, port: int):
    import logging
    import os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    from config.settings import load_settings
    load_settings(web_mode=True)

    # Mock mode: point config at the built-in mock routes so no real API keys
    # are needed.  Set VOXRAD_MOCK_MODE=1 to enable.
    if os.environ.get("VOXRAD_MOCK_MODE"):
        from config.config import config
        mock_base = f"http://localhost:{port}/mock/v1"
        config.BASE_URL = mock_base
        config.TRANSCRIPTION_BASE_URL = mock_base
        config.TEXT_API_KEY = "mock"
        config.TRANSCRIPTION_API_KEY = "mock"
        config.SELECTED_MODEL = "gpt-mock"
        config.SELECTED_TRANSCRIPTION_MODEL = "whisper-mock"
        logging.getLogger(__name__).info(
            "[mock] Mock mode active — API calls routed to %s", mock_base
        )

    import uvicorn
    from web.app import app as web_app

    url = f"http://{'localhost' if host == '0.0.0.0' else host}:{port}/"
    print(f"\n  VoxRad web server → {url}\n")
    uvicorn.run(web_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoxRad — Voice Radiology Dictation")
    parser.add_argument(
        "--web", action="store_true",
        help="Launch web server instead of the desktop UI"
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Web server bind address (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Web server port (default: 8765)"
    )
    args = parser.parse_args()

    if args.web:
        _run_web(args.host, args.port)
    else:
        _run_desktop()
