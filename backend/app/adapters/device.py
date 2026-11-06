# Read-only device adapter contract. No CNC control/write operations.
from typing import Protocol,Any
class DeviceReadAdapter(Protocol):
 async def connect(self)->None: ...
 async def read_tags(self,names:list[str])->dict[str,Any]: ...
 async def close(self)->None: ...
class MockDeviceAdapter:
 async def connect(self): pass
 async def read_tags(self,names):
  sample={"machine_status":"running","spindle_load_pct":42.0,"cycle_count":128}
  return {k:sample.get(k) for k in names}
 async def close(self): pass
