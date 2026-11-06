import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path
import httpx
import yaml
from .outbox import Outbox
from .adapters.mock import MockAdapter
from .adapters.mtconnect import MTConnectAdapter
from .adapters.opcua import OPCUAAdapter

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("cnc-mes-edge")

# API responses that will never succeed on retry: bad payload, unknown machine,
# bad token, duplicate content. These go straight to the dead letter.
PERMANENT_CODES = {400, 401, 403, 404, 409, 411, 413, 415, 422}


def _permanent(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code in PERMANENT_CODES


async def flush_outbox(client: httpx.AsyncClient, api: str, token: str, outbox: Outbox, batch_size: int = 500, max_attempts: int = 20) -> int:
    """Send queued events in order and delete only after API acknowledgement.

    Transient errors (network/5xx) pause the queue at the failing message and
    retry on the next poll. Permanent 4xx errors and messages that exceed
    max_attempts are moved to the dead letter so one bad message cannot block
    the rest of the queue forever.
    """
    sent = 0
    for item in outbox.pending(limit=batch_size):
        try:
            response = await client.post(
                api + "/api/v3/edge/ingest",
                headers={"X-Edge-Token": token},
                json=json.loads(item["payload"]),
            )
            response.raise_for_status()
            outbox.delete(item["id"])
            sent += 1
        except Exception as exc:
            attempts = outbox.mark_attempt(item["id"], str(exc))
            if _permanent(exc) or attempts >= max_attempts:
                outbox.dead_letter(item["id"], str(exc))
                LOG.error("outbox message %s moved to dead letter after %s attempts: %s", item["message_id"], attempts, exc)
                continue  # keep delivering the messages behind it
            LOG.error("outbox delivery paused at message=%s attempts=%s error=%s", item["message_id"], attempts, exc)
            break  # transient error: preserve order, retry the whole queue next poll
    return sent

async def run():
    config_path = Path(os.getenv("EDGE_CONFIG", "/app/config.yaml"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    api = os.getenv("MES_API_BASE_URL", "http://api:8000").rstrip("/")
    token = os.getenv("EDGE_INGEST_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("EDGE_INGEST_TOKEN must be a random secret of at least 32 characters")
    poll_seconds = max(1, int(config.get("poll_seconds", 5)))
    gateway_id = config.get("gateway_id", socket.gethostname())
    version = "3.0.0"
    outbox = Outbox(os.getenv("EDGE_OUTBOX_DB", "/data/edge-outbox.db"))
    adapters = {"mock": MockAdapter(), "mtconnect": MTConnectAdapter(), "opcua": OPCUAAdapter()}
    timeout = httpx.Timeout(8.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            started = time.monotonic()
            await flush_outbox(client, api, token, outbox)
            for machine in config.get("machines", []):
                kind = machine.get("adapter", "mock").lower()
                adapter = adapters.get(kind)
                if not adapter:
                    LOG.error("unsupported adapter=%s machine=%s", kind, machine.get("machine_code"))
                    continue
                try:
                    event = await adapter.read(machine)
                    if event:
                        raw_state = event.get("state")
                        state_map = machine.get("state_map", {})
                        if raw_state is not None and str(raw_state) in state_map:
                            event["state"] = state_map[str(raw_state)]
                        event.update({"machine_code": machine["machine_code"], "source": f"edge:{kind}"})
                        outbox.enqueue(event)
                except Exception:
                    LOG.exception("collection failed for %s", machine.get("machine_code", "unknown"))
            await flush_outbox(client, api, token, outbox)
            try:
                await client.post(api + "/api/v3/edge/heartbeat", headers={"X-Edge-Token": token}, json={
                    "gateway_id": gateway_id, "version": version,
                    "ip_address": socket.gethostbyname(socket.gethostname()),
                    "message": f"collector active; outbox_pending={outbox.count()}; oldest={outbox.oldest_created_at() or 'none'}",
                })
            except Exception as exc:
                LOG.warning("heartbeat failed: %s", exc)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.1, poll_seconds - elapsed))

if __name__ == "__main__":
    asyncio.run(run())
