"""Generate a QR code PNG that opens the Beanthentic Client Web on phone (same Wi-Fi)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.client_qr import ensure_client_qr_files, resolve_client_web_url  # noqa: E402


def main() -> int:
    url = resolve_client_web_url()
    files = ensure_client_qr_files(url)
    print("Beanthentic Client Web QR generated")
    print(f"URL: {files['target_url']}")
    print(f"QR (plain): {files['plain']}")
    print(f"QR (print): {files['print']}")
    print()
    print("Download in browser:")
    print(f"  http://127.0.0.1:5001/download/client-website-qr")
    print(f"  http://127.0.0.1:5001/client-qr")
    print()
    print("Use the same home Wi-Fi on phone and laptop.")
    print("Run scripts/run-for-phone.bat before scanning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
