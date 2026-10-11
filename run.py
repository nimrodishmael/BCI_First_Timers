"""Start the BCI First Timers app.

    python run.py            start the app (opens your browser)
    python run.py --no-browser
    python run.py --demo     explore with made-up data in a separate database
"""
import argparse
import os
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from waitress import serve  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="BCI First Timers")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--demo", action="store_true", help="use made-up data in the data_demo folder")
    args = parser.parse_args()

    if args.demo:
        # Separate database, no real SMS, different port so it can run next to the real app.
        os.environ["DATA_DIR"] = "data_demo"
        os.environ["SMS_PROVIDER"] = "test"
        os.environ["PORT"] = "5050"
        import demo_data
        demo_data.main()

    from bci_app import create_app

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app = create_app(start_scheduler=True)

    local_url = f"http://localhost:{port}"
    print()
    print("  BCI First Timers is running" + (" with DEMO data (sign in as demo / demo1234)." if args.demo else "."))
    print(f"  Open {local_url} in your browser.")
    if host == "0.0.0.0":
        print("  Other computers on the office network can use this computer's IP address instead of localhost.")
    print(f"  SMS mode: {app.config['SMS_PROVIDER']}")
    print("  Keep this window open. Closing it stops the app and the SMS reminders.")
    print()

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(local_url)).start()
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
