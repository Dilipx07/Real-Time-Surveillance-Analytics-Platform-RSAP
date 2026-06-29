from typing import Any


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, Any] = {}

    async def connect(self, user_id: str, websocket: Any) -> None:
        previous = self.connections.get(user_id)
        if previous is not None:
            await previous.close(code=1000, reason="Replaced by a newer connection")
        await websocket.accept()
        self.connections[user_id] = websocket

    def disconnect(self, user_id: str, websocket: Any) -> None:
        if self.connections.get(user_id) is websocket:
            self.connections.pop(user_id, None)


manager = ConnectionManager()
