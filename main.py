# main.py

import sys
import asyncio
import threading
from PySide6.QtWidgets import QApplication
from core.main_window import MainWindow
from client.client import MCPClient


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def main():

    # 创建后台 event loop
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=start_loop, args=(loop,), daemon=True)
    t.start()

    # 初始化 MCP（在后台 loop）
    async def init():
        client = MCPClient()

        await client.connect_to_server(
            r"D:\finalwork\PythonProject\server\server.py"
        )

        return client

    future = asyncio.run_coroutine_threadsafe(init(), loop)
    mcp_client = future.result()

    app = QApplication(sys.argv)
    window = MainWindow(mcp_client, loop)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()