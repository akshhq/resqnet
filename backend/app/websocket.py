from typing import List
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # print("WS CONNECTED | total:", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        # print("WS DISCONNECTED | total:", len(self.active_connections))

    async def broadcast(self, message: dict):
        # print("BROADCAST CALLED | connections:", len(self.active_connections))
        for connection in self.active_connections:
            await connection.send_json(message)
