"""
websocket.py — WebSocket connection manager for ResQNet.

Provides the ConnectionManager class used by main.py to broadcast
device update payloads to all connected dashboard clients.
"""

from typing import List
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    # Per-connection error handling so a single dropped client
    # doesn't abort the broadcast loop and starve remaining connections.
    # Dead connections are collected and removed after the loop.
    async def broadcast(self, message: dict):
        dead: List[WebSocket] = []

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"⚠️  Broadcast failed for a connection, removing. Reason: {e}")
                dead.append(connection)

        for conn in dead:
            self.disconnect(conn)