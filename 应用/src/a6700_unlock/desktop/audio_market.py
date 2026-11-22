"""Device-independent local audio catalog and preview worker for the desktop app."""
import base64
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from .protocol import decode_request
from .update_service import DesktopUpdateService


def main():
    try:
        request = decode_request(sys.stdin.readline())
        command = request["command"]
        if command == "list_audio":
            result = DesktopUpdateService().audio_catalog()
        elif command == "preview_audio":
            with TemporaryDirectory(prefix="a6700-market-preview-") as directory:
                result = DesktopUpdateService().audio_preview(
                    request.get("package_id"), request.get("sound_id"), preview_dir=directory
                )
                if result["status"] == "ready":
                    path = Path(result.pop("path"))
                    result["wav"] = base64.b64encode(path.read_bytes()).decode("ascii")
        else:
            raise ValueError("音频市场命令不可用。")
    except (OSError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
