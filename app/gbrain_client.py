import os
import subprocess
import logging

logger = logging.getLogger("destillo.gbrain")

GBRAIN_CONTAINER = "gbrain-server"
GBRAIN_MOUNT = "/destillo"


def ingest(filepath: str) -> bool:
    """Notify gbrain about a new markdown file by triggering a sync."""
    if not os.path.exists(filepath):
        logger.warning(f"File not found, skipping gbrain ingest: {filepath}")
        return False

    try:
        result = subprocess.run(
            ["docker", "exec", GBRAIN_CONTAINER,
             "bun", "run", "/app/src/cli.ts", "sync",
             "--repo", GBRAIN_MOUNT,
             "--no-embed", "--skip-failed", "--no-hard-deadline"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            logger.info(f"gbrain sync completed after write: {filepath}")
            return True
        else:
            logger.warning(f"gbrain sync returned {result.returncode}: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("gbrain sync timed out after 60s")
        return False
    except FileNotFoundError:
        logger.warning("docker not available for gbrain sync")
        return False
    except Exception as e:
        logger.warning(f"gbrain sync failed: {e}")
        return False
