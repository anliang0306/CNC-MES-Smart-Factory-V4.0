from datetime import datetime, timezone
from uuid import uuid4
import random

class MockAdapter:
    """Synthetic telemetry for commissioning and API tests; never controls equipment."""
    def __init__(self):
        self.counters = {}
        self.tick = {}
    async def read(self, machine: dict):
        code = machine["machine_code"]
        self.tick[code] = self.tick.get(code, 0) + 1
        current = self.counters.setdefault(code, {"good": 0, "reject": 0})
        state = "running" if self.tick[code] % 9 not in (0, 1) else "idle"
        if state == "running":
            current["good"] += random.randint(0, 2)
            if random.random() < 0.04:
                current["reject"] += 1
        metrics = [
            {"name": "spindle_load_pct", "value": round(random.uniform(28, 82), 1), "quality": "good"},
            {"name": "spindle_speed_rpm", "value": random.randint(1800, 5200), "quality": "good"},
            {"name": "cycle_time_seconds", "value": round(random.uniform(45, 110), 1), "quality": "good"},
            {"name": "ideal_cycle_seconds", "value": 72.0, "quality": "good"},
            {"name": "good_count_total", "value": current["good"], "quality": "good"},
            {"name": "reject_count_total", "value": current["reject"], "quality": "good"},
        ]
        return {"message_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "state": state, "state_reason": "mock simulator", "metrics": metrics, "alarms": []}
