from datetime import datetime, timezone
from uuid import uuid4
import httpx
import xml.etree.ElementTree as ET

class MTConnectAdapter:
    """MTConnect read-only /current polling adapter. data_items maps dataItemId to MES point name."""
    async def read(self, machine: dict):
        endpoint = machine["endpoint"].rstrip("/") + "/current"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(endpoint)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        wanted = machine.get("data_items", {})
        state_id = machine.get("state_data_item_id", "")
        metrics = []
        state = None
        for elem in root.iter():
            item_id = elem.attrib.get("dataItemId")
            if not item_id or elem.text is None:
                continue
            raw = elem.text.strip()
            if item_id == state_id:
                state = raw
            elif item_id in wanted:
                name = wanted[item_id]
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
                metrics.append({"name": name, "value": value, "quality": "good"})
        return {"message_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "state": state, "metrics": metrics}
