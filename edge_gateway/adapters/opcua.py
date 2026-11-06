from datetime import datetime, timezone
from uuid import uuid4
import os

class OPCUAAdapter:
    """OPC UA read-only polling adapter. Configure endpoint and node_map in config.yaml."""
    async def read(self, machine: dict):
        try:
            from asyncua import Client
        except ImportError as exc:
            raise RuntimeError("Install asyncua to enable OPC UA adapter") from exc
        values = []
        state = None
        client = Client(url=machine["endpoint"])
        # Credentials are injected from process environment, never stored in YAML.
        username_env = machine.get("username_env", "")
        password_env = machine.get("password_env", "")
        if username_env and os.getenv(username_env):
            client.set_user(os.environ[username_env])
        if password_env and os.getenv(password_env):
            client.set_password(os.environ[password_env])
        async with client:
            for metric_name, node_id in machine.get("node_map", {}).items():
                value = await client.get_node(node_id).read_value()
                if metric_name == "machine_state":
                    state = str(value)
                else:
                    values.append({"name": metric_name, "value": value if isinstance(value, (int, float, str, bool)) else str(value), "quality": "good"})
        return {"message_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "state": state, "metrics": values}
