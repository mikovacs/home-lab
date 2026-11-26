import os
import stat
import sys
from pathlib import Path


def check(root="/data", device="/expected-device"):
    expected = os.stat(device)
    actual = os.stat(root)
    if not stat.S_ISBLK(expected.st_mode) or actual.st_dev != expected.st_rdev:
        raise RuntimeError("Media filesystem does not match expected UUID device")
    for rel in (() if os.environ.get("GUARD_KIND") == "data" else ("media/movies", "media/tv", "downloads/torrents/complete/movies",
                "downloads/torrents/complete/tv", "downloads/torrents/incomplete")):
        p = Path(root) / rel
        if not p.is_dir() or p.resolve() != p or p.stat().st_dev != actual.st_dev:
            raise RuntimeError("Missing, redirected or cross-filesystem media directory: " + rel)
    return True


if __name__ == "__main__":
    try:
        check()
    except (OSError, RuntimeError) as exc:
        sys.exit(str(exc))
