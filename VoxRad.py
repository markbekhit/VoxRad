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

    if os.environ.get("VOXRAD_MOCK_MODE"):
        logging.getLogger(__name__).info(
            "[mock] Mock mode active — transcribe and format return canned responses"
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
