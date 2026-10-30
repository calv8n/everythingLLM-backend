from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.event_bus import get_bus, remove_bus

router = APIRouter()

@router.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    bus = get_bus(task_id)
    if not bus:
        await websocket.close(code=1008)  # Policy Violation
        return

    await websocket.accept()
    try:
        async for event in bus.subscribe():
            await websocket.send_json(event)
        await websocket.close(code=1000, reason="Task complete")
    except WebSocketDisconnect:
        pass
    finally:
        remove_bus(task_id)
