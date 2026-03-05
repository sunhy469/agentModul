"""
main_window.py - 主窗口类
"""

import asyncio
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QVBoxLayout,
    QTextEdit, QPushButton, QHBoxLayout, QScrollArea
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon


class MainWindow(QMainWindow):

    result_signal = Signal(str)

    def __init__(self, mcpClient, loop):
        super().__init__()
        self.content = ""  # 存储用户输入的文本
        self.mcpClient = mcpClient
        self.loop = loop
        self.result_signal.connect(self.show_ai_result)
        self.setWindowIcon(QIcon("icon.png"))
        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        # 设置窗口属性
        self.setWindowTitle("智能助手")
        self.setGeometry(100, 100, 600, 400)

        # 创建中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 创建主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)

        # 添加标题
        title_label = QLabel("🤖 多模态智能助手")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 24px;
                color: #2E7D32;
                font-weight: bold;
                padding: 20px;
                text-align: center;
            }
        """)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)

        # 创建滚动区域用于显示历史
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 创建显示历史内容的部件
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history_layout.setSpacing(5)

        self.scroll_area.setWidget(self.history_container)
        main_layout.addWidget(self.scroll_area, 1)  # 添加伸缩因子

        # 创建输入区域
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_layout.setSpacing(5)

        # 添加输入框标签
        input_label = QLabel("请输入内容：")
        input_label.setStyleSheet("font-weight: bold;")

        # 创建多行文本框
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("在这里输入您的内容...")
        self.text_input.setMaximumHeight(100)
        self.text_input.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ccc;
                border-radius: 5px;
                padding: 8px;
                font-size: 14px;
            }
            QTextEdit:focus {
                border: 2px solid #4CAF50;
            }
        """)

        # 创建按钮布局
        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)

        # 发送按钮
        send_button = QPushButton("发送")
        send_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        send_button.clicked.connect(self.send_message)

        # 清空按钮
        clear_button = QPushButton("清空")
        clear_button.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 14px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        clear_button.clicked.connect(self.clear_input)

        # 将按钮添加到布局
        button_layout.addStretch()
        button_layout.addWidget(clear_button)
        button_layout.addWidget(send_button)

        # 将组件添加到输入布局
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.text_input)
        input_layout.addWidget(button_widget)

        # 将输入区域添加到主布局
        main_layout.addWidget(input_widget)


    def send_message(self):

        self.content = self.text_input.toPlainText().strip()
        self.add_to_history(f"用户: {self.content}")

        if self.content:

            self.text_input.clear()

            future = asyncio.run_coroutine_threadsafe(
                self.mcpClient.process_query(self.content),
                self.loop
            )

            future.add_done_callback(self.handle_result)
        else:
            self.status_label.setText("请输入内容再发送！")

    def handle_result(self, future):

        try:
            result = future.result()
        except Exception as e:
            result = f"错误: {e}"

        # 发射信号（线程安全）
        self.result_signal.emit(result)

    def show_ai_result(self, result):
        self.add_to_history(f"AI: {result}")


    def clear_input(self):
        """清空输入框"""
        self.text_input.clear()
        self.status_label.setText("输入框已清空")

    def add_to_history(self, message):
        """添加消息到历史区域"""
        label = QLabel(message)
        label.setStyleSheet("""
            QLabel {
                background-color: #f0f8ff;
                border: 1px solid #d0e0ff;
                border-radius: 5px;
                padding: 10px;
                margin: 2px;
                font-size: 14px;
            }
        """)
        label.setWordWrap(True)  # 自动换行
        label.setMaximumWidth(self.scroll_area.width() - 20)  # 考虑滚动条宽度

        # 添加到历史布局
        self.history_layout.addWidget(label)

        # 确保最新消息可见
        self.ensure_scroll_to_bottom()

    def ensure_scroll_to_bottom(self):
        """确保滚动到最底部"""
        # 等待布局更新
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        """滚动到底部的实际实现"""
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def clear_history(self):
        """清空历史记录"""
        # 移除所有子部件
        for i in reversed(range(self.history_layout.count())):
            widget = self.history_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        self.status_label.setText("历史记录已清空")