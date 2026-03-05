"""
main_window.py - 主窗口类
"""

import asyncio
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QVBoxLayout,
    QTextEdit, QPushButton, QHBoxLayout, QScrollArea, QFileDialog,
    QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon


class MainWindow(QMainWindow):

    result_signal = Signal(str)

    def __init__(self, mcpClient, loop):
        super().__init__()
        self.content = ""  # 存储用户输入的文本
        self.selected_file_path = ""
        self.mcpClient = mcpClient
        self.loop = loop
        self.result_signal.connect(self.show_ai_result)
        self.setWindowIcon(QIcon("icon.png"))
        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        # 设置窗口属性
        self.setWindowTitle("智能助手")
        self.setMinimumSize(920, 640)
        self.resize(1080, 760)

        # 全局样式：更柔和的配色和圆角
        self.setStyleSheet("""
            QMainWindow {
                background: #f4f6fb;
            }
            QWidget {
                font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
                color: #263238;
            }
            QFrame#Card {
                background: #ffffff;
                border: 1px solid #e7ebf3;
                border-radius: 16px;
            }
            QLabel#TitleLabel {
                font-size: 28px;
                font-weight: 700;
                color: #1f2d3d;
            }
            QLabel#SubTitleLabel {
                font-size: 13px;
                color: #6b7280;
            }
            QTextEdit {
                background: #ffffff;
                border: 1px solid #d6dce8;
                border-radius: 12px;
                padding: 10px;
                font-size: 14px;
                selection-background-color: #d6e7ff;
            }
            QTextEdit:focus {
                border: 2px solid #5b8def;
            }
            QPushButton {
                border: none;
                border-radius: 12px;
                padding: 9px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#PrimaryButton {
                color: white;
                background: #4f7df3;
            }
            QPushButton#PrimaryButton:hover {
                background: #3f6ee8;
            }
            QPushButton#DangerButton {
                color: white;
                background: #ef5350;
            }
            QPushButton#DangerButton:hover {
                background: #e53935;
            }
            QPushButton#AssistButton {
                color: #2157c7;
                background: #e8f0ff;
                border: 1px solid #c6d8ff;
            }
            QPushButton#AssistButton:hover {
                background: #dbe8ff;
            }
            QLabel#StatusLabel {
                background: #eef7ff;
                border: 1px solid #d4e8ff;
                border-radius: 10px;
                color: #2d5d9f;
                padding: 8px 10px;
                font-size: 12px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #eef1f7;
                width: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #c2cad8;
                border-radius: 5px;
                min-height: 24px;
            }
        """)

        # 创建中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 创建主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(22, 18, 22, 18)
        main_layout.setSpacing(14)

        # 顶部信息区
        title_card = QFrame()
        title_card.setObjectName("Card")
        title_layout = QVBoxLayout(title_card)
        title_layout.setContentsMargins(20, 16, 20, 16)
        title_layout.setSpacing(4)

        title_label = QLabel("🤖 多模态智能助手")
        title_label.setObjectName("TitleLabel")

        subtitle_label = QLabel("支持文本提问、文件上传与智能解析，让交互更自然。")
        subtitle_label.setObjectName("SubTitleLabel")

        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        main_layout.addWidget(title_card)

        # 聊天记录卡片
        history_card = QFrame()
        history_card.setObjectName("Card")
        history_card_layout = QVBoxLayout(history_card)
        history_card_layout.setContentsMargins(14, 14, 14, 14)
        history_card_layout.setSpacing(8)

        history_header = QLabel("会话记录")
        history_header.setStyleSheet("font-size: 14px; font-weight: 700; color: #34495e;")
        history_card_layout.addWidget(history_header)

        # 创建滚动区域用于显示历史
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 创建显示历史内容的部件
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history_layout.setSpacing(8)

        self.scroll_area.setWidget(self.history_container)
        history_card_layout.addWidget(self.scroll_area, 1)
        main_layout.addWidget(history_card, 1)

        # 输入卡片
        input_card = QFrame()
        input_card.setObjectName("Card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 14, 16, 14)
        input_layout.setSpacing(10)

        input_label = QLabel("请输入内容")
        input_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #34495e;")

        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("例如：帮我总结这个文件的要点，并给出下一步执行建议...")
        self.text_input.setMinimumHeight(110)
        self.text_input.setMaximumHeight(150)

        # 文件上传区
        file_widget = QWidget()
        file_layout = QHBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(10)

        upload_button = QPushButton("上传文件/图片")
        upload_button.setObjectName("AssistButton")
        upload_button.setMinimumWidth(130)
        upload_button.clicked.connect(self.select_file)

        self.file_path_label = QLabel("未选择文件")
        self.file_path_label.setStyleSheet(
            "font-size: 12px; color: #5f6b7a; background: #f7f9fc; border: 1px solid #e3e8f2;"
            "border-radius: 10px; padding: 7px 10px;"
        )

        file_layout.addWidget(upload_button)
        file_layout.addWidget(self.file_path_label, 1)

        # 操作按钮
        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(10)

        clear_button = QPushButton("清空")
        clear_button.setObjectName("DangerButton")
        clear_button.setMinimumWidth(92)
        clear_button.clicked.connect(self.clear_input)

        send_button = QPushButton("发送")
        send_button.setObjectName("PrimaryButton")
        send_button.setMinimumWidth(92)
        send_button.clicked.connect(self.send_message)

        button_layout.addStretch()
        button_layout.addWidget(clear_button)
        button_layout.addWidget(send_button)

        # 状态信息
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("StatusLabel")

        input_layout.addWidget(input_label)
        input_layout.addWidget(self.text_input)
        input_layout.addWidget(file_widget)
        input_layout.addWidget(button_widget)
        input_layout.addWidget(self.status_label)

        main_layout.addWidget(input_card)

    def send_message(self):

        self.content = self.text_input.toPlainText().strip()
        has_file = bool(self.selected_file_path)

        if self.content:
            self.add_to_history(f"用户: {self.content}", role="user")
        if has_file:
            self.add_to_history(f"用户上传文件: {os.path.basename(self.selected_file_path)}", role="user")

        if self.content or has_file:

            self.text_input.clear()
            self.status_label.setText("正在发送请求，请稍候...")

            future = asyncio.run_coroutine_threadsafe(
                self.mcpClient.process_query(self.content, self.selected_file_path),
                self.loop
            )

            future.add_done_callback(self.handle_result)
        else:
            self.status_label.setText("请输入内容或先选择文件后再发送。")

    def handle_result(self, future):

        try:
            result = future.result()
        except Exception as e:
            result = f"错误: {e}"

        # 发射信号（线程安全）
        self.result_signal.emit(result)

    def show_ai_result(self, result):
        self.status_label.setText("已完成")
        self.selected_file_path = ""
        self.file_path_label.setText("未选择文件")
        self.add_to_history(f"AI: {result}", role="ai")

    def clear_input(self):
        """清空输入框"""
        self.text_input.clear()
        self.selected_file_path = ""
        self.file_path_label.setText("未选择文件")
        self.status_label.setText("输入内容与文件选择已清空")

    def select_file(self):
        """选择要上传并解析的文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件或图片",
            "",
            "支持文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.pdf *.txt *.md *.docx);;所有文件 (*)"
        )

        if file_path:
            self.selected_file_path = file_path
            self.file_path_label.setText(os.path.basename(file_path))
            self.status_label.setText("文件已选择，点击发送进行解析")

    def add_to_history(self, message, role="user"):
        """添加消息到历史区域"""
        label = QLabel(message)
        if role == "ai":
            style = """
                QLabel {
                    background-color: #eef6ff;
                    border: 1px solid #d7e7ff;
                    border-radius: 12px;
                    padding: 10px;
                    margin: 2px;
                    font-size: 14px;
                    color: #244a7f;
                }
            """
        else:
            style = """
                QLabel {
                    background-color: #f8fbff;
                    border: 1px solid #e1e9f7;
                    border-radius: 12px;
                    padding: 10px;
                    margin: 2px;
                    font-size: 14px;
                }
            """
        label.setStyleSheet(style)
        label.setWordWrap(True)  # 自动换行
        label.setMaximumWidth(max(self.scroll_area.width() - 30, 600))

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
