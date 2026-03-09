# main.py

import asyncio
import os
import sys
import threading

from PySide6.QtWidgets import QApplication

from client.client import MCPClient
from core.main_window import MainWindow


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def main():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=start_loop, args=(loop,), daemon=True)
    thread.start()

    async def init():
        client = MCPClient()
        server_script = os.getenv("MCP_SERVER_SCRIPT", os.path.join(os.getcwd(), "server", "server.py"))
        await client.connect_to_server(server_script)
        return client

    future = asyncio.run_coroutine_threadsafe(init(), loop)
    mcp_client = future.result()

    app = QApplication(sys.argv)
    window = MainWindow(mcp_client, loop)
    window.show()

    exit_code = app.exec()

    try:
        asyncio.run_coroutine_threadsafe(mcp_client.close(), loop).result(timeout=5)
    finally:
        loop.call_soon_threadsafe(loop.stop)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()