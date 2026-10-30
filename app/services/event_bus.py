import asyncio
from typing import AsyncGenerator, Dict

class EventBus:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def publish(self, event: Dict):
        """Publish an event to the bus."""
        await self.queue.put(event)

    async def finish(self):
        """Signal that no more events will be published."""
        await self.queue.put(None)

    async def subscribe(self) -> AsyncGenerator[Dict, None]:
        """Subscribe to events from the bus."""
        while True:
            event = await self.queue.get()
            if event is None:
                break
            yield event

_buses: Dict[str, EventBus] = {}

def create_bus(task_id: str) -> EventBus:
    """Create a new event bus for a given task ID."""
    bus = EventBus()
    _buses[task_id] = bus
    return bus

def get_bus(task_id: str) -> EventBus:
    """Retrieve an existing event bus for a given task ID."""
    return _buses.get(task_id)

def remove_bus(task_id: str):
    _buses.pop(task_id, None)
