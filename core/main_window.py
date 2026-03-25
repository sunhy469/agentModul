"""
main_window.py - 主窗口类
"""

import asyncio
import os
import tempfile
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QVBoxLayout,
    QTextEdit, QPushButton, QHBoxLayout, QScrollArea, QFileDialog,
    QFrame, QSizePolicy, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QAudioInput, QMediaCaptureSession, QMediaDevices, QMediaFormat, QMediaRecorder


class MainWindow(QMainWindow):

    result_signal = Signal(str)
    voice_signal = Signal(str)

    def __init__(self, mcpClient, loop):
        super().__init__()
        self.content = ""
        self.selected_file_path = ""
        self.mcpClient = mcpClient
        self.loop = loop
        self.result_signal.connect(self.show_ai_result)
        self.voice_signal.connect(self.on_voice_transcribed)
        self.is_recording = False
        self.record_audio_path = ""
        self.audio_input = None
        self.capture_session = None
        self.media_recorder = None
        self.setWindowIcon(QIcon("icon.png"))
        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("MAI Copilot")
        self.setMinimumSize(920, 640)
        self.resize(1080, 760)

        self.setStyleSheet("""
            QMainWindow { background: #f4f6fb; }
            QWidget { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; color: #263238; }
            QFrame#Card { background: #ffffff; border: 1px solid #e7ebf3; border-radius: 16px; }
            QTextEdit { background: #ffffff; border: 1px solid #d6dce8; border-radius: 12px; padding: 10px; font-size: 14px; selection-background-color: #d6e7ff; }
            QTextEdit:focus { border: 2px solid #5b8def; }
            QPushButton { border: none; border-radius: 14px; padding: 8px 12px; font-size: 13px; font-weight: 600; }
            QPushButton#PrimaryButton { color: white; background: #4f7df3; }
            QPushButton#PrimaryButton:hover { background: #3f6ee8; }
            QPushButton#DangerButton { color: white; background: #ef5350; }
            QPushButton#DangerButton:hover { background: #e53935; }
            QPushButton#AssistButton { color: #2157c7; background: #e8f0ff; border: 1px solid #c6d8ff; }
            QPushButton#AssistButton:hover { background: #dbe8ff; }
            QLabel#StatusLabel { background: #eef7ff; border: 1px solid #d4e8ff; border-radius: 10px; color: #2d5d9f; padding: 8px 10px; font-size: 12px; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #eef1f7; width: 10px; border-radius: 5px; margin: 2px; }
            QScrollBar::handle:vertical { background: #c2cad8; border-radius: 5px; min-height: 24px; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(22, 18, 22, 18)
        main_layout.setSpacing(14)

        history_card = QFrame()
        history_card.setObjectName("Card")
        history_card_layout = QVBoxLayout(history_card)
        history_card_layout.setContentsMargins(14, 14, 14, 14)
        history_card_layout.setSpacing(8)

        history_header = QLabel("会话记录")
        history_header.setStyleSheet("font-size: 14px; font-weight: 700; color: #34495e;")
        history_card_layout.addWidget(history_header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history_layout.setSpacing(8)

        self.scroll_area.setWidget(self.history_container)
        history_card_layout.addWidget(self.scroll_area, 1)
        main_layout.addWidget(history_card, 6)

        input_card = QFrame()
        input_card.setObjectName("Card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 14, 16, 14)
        input_layout.setSpacing(10)

        input_label = QLabel("请输入内容")
        input_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #34495e;")

        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("例如：结合历史上下文，帮我继续完善刚才的计划，并调用工具补充信息...")
        self.text_input.setMinimumHeight(72)
        self.text_input.setMaximumHeight(96)

        file_widget = QWidget()
        file_layout = QHBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(10)

        upload_button = QPushButton("📎")
        upload_button.setObjectName("AssistButton")
        upload_button.setFixedSize(40, 36)
        upload_button.setToolTip("上传文件/图片")
        upload_button.clicked.connect(self.select_file)

        self.voice_button = QPushButton("🎤")
        self.voice_button.setObjectName("AssistButton")
        self.voice_button.setFixedSize(40, 36)
        self.voice_button.setToolTip("开始录音")
        self.voice_button.clicked.connect(self.toggle_microphone_recording)

        self.file_path_label = QLabel("未选择文件")
        self.file_path_label.setStyleSheet(
            "font-size: 12px; color: #5f6b7a; background: #f7f9fc; border: 1px solid #e3e8f2;"
            "border-radius: 10px; padding: 6px 8px;"
        )

        file_layout.addWidget(upload_button)
        file_layout.addWidget(self.voice_button)
        file_layout.addWidget(self.file_path_label, 1)

        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(10)

        self.status_label = QLabel("就绪（已启用文件记忆）")
        self.status_label.setObjectName("StatusLabel")
        button_layout.addWidget(self.status_label)

        button_layout.addStretch()

        clear_memory_button = QPushButton("重置记忆")
        clear_memory_button.setObjectName("AssistButton")
        clear_memory_button.setToolTip("删除文件中的上下文记忆，并清空当前显示历史")
        clear_memory_button.clicked.connect(self.clear_conversation_memory)
        button_layout.addWidget(clear_memory_button)

        clear_button = QPushButton("🗑")
        clear_button.setObjectName("DangerButton")
        clear_button.setFixedSize(40, 36)
        clear_button.setToolTip("清空输入")
        clear_button.clicked.connect(self.clear_input)
        button_layout.addWidget(clear_button)

        send_button = QPushButton("➤")
        send_button.setObjectName("PrimaryButton")
        send_button.setFixedSize(48, 36)
        send_button.setToolTip("发送")
        send_button.clicked.connect(self.send_message)
        button_layout.addWidget(send_button)

        input_layout.addWidget(input_label)
        input_layout.addWidget(self.text_input)
        input_layout.addWidget(file_widget)
        input_layout.addWidget(button_widget)

        main_layout.addWidget(input_card, 1)

        self.setup_microphone_recording()

    def send_message(self):
        self.content = self.text_input.toPlainText().strip()
        has_file = bool(self.selected_file_path)

        if self.content:
            self.add_to_history(self.content, role="user")
        if has_file:
            self.add_to_history(f"📎 上传文件：{os.path.basename(self.selected_file_path)}", role="user")

        if self.content or has_file:
            self.text_input.clear()
            self.status_label.setText("Agent 正在结合历史记忆处理请求，请稍候...")

            future = asyncio.run_coroutine_threadsafe(
                self.mcpClient.process_query(self.content, self.selected_file_path),
                self.loop,
            )
            future.add_done_callback(self.handle_result)
        else:
            self.status_label.setText("请输入内容或先选择文件后再发送。")

    def handle_result(self, future):
        try:
            result = future.result()
        except Exception as e:
            result = f"错误: {e}"
        self.result_signal.emit(result)

    def show_ai_result(self, result):
        self.status_label.setText("已完成，并写入上下文记忆")
        self.selected_file_path = ""
        self.file_path_label.setText("未选择文件")
        self.add_to_history(result, role="ai")

    def clear_input(self):
        if self.text_input.toPlainText().strip() or self.selected_file_path:
            reply = QMessageBox.question(
                self,
                "确认清空",
                "确定要清空当前输入与文件选择吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.text_input.clear()
        self.selected_file_path = ""
        self.file_path_label.setText("未选择文件")
        self.status_label.setText("输入内容与文件选择已清空")

    def clear_conversation_memory(self):
        reply = QMessageBox.warning(
            self,
            "确认重置记忆",
            "这会清空对话历史与本地记忆文件，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.status_label.setText("已取消重置记忆")
            return
        self.mcpClient.clear_memory()
        self.clear_history()
        self.status_label.setText("文件记忆与当前会话展示均已清空")

    def select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件或图片",
            "",
            "支持文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.pdf *.txt *.md *.docx);;所有文件 (*)",
        )

        if file_path:
            self.selected_file_path = file_path
            self.file_path_label.setText(os.path.basename(file_path))
            self.status_label.setText("文件已选择，点击发送进行解析")

    def setup_microphone_recording(self):
        """初始化麦克风录音组件。"""
        try:
            devices = QMediaDevices.audioInputs()
            if not devices:
                self.status_label.setText("未检测到麦克风设备，语音功能不可用")
                self.voice_button.setEnabled(False)
                return

            self.audio_input = QAudioInput(devices[0])
            self.capture_session = QMediaCaptureSession()
            self.capture_session.setAudioInput(self.audio_input)

            self.media_recorder = QMediaRecorder()
            self.capture_session.setRecorder(self.media_recorder)
            self.media_recorder.recorderStateChanged.connect(self.on_recorder_state_changed)
        except Exception as e:
            self.status_label.setText(f"初始化麦克风失败: {e}")
            self.voice_button.setEnabled(False)

    def toggle_microphone_recording(self):
        """开始或停止麦克风录音。"""
        if not self.media_recorder:
            self.status_label.setText("语音功能不可用，请检查麦克风或多媒体组件")
            return

        if not self.is_recording:
            temp_dir = tempfile.gettempdir()
            self.record_audio_path = os.path.join(temp_dir, "assistant_record.wav")

            output_url = QUrl.fromLocalFile(self.record_audio_path)
            self.media_recorder.setOutputLocation(output_url)
            self.media_recorder.setMediaFormat(QMediaFormat(QMediaFormat.FileFormat.Wave))
            self.media_recorder.record()

            self.is_recording = True
            self.voice_button.setText("⏹")
            self.voice_button.setToolTip("停止录音并识别")
            self.status_label.setText("正在录音... 再次点击可停止并发送")
        else:
            self.media_recorder.stop()
            self.is_recording = False
            self.voice_button.setText("🎤")
            self.voice_button.setToolTip("开始录音")
            self.status_label.setText("录音结束，正在识别语音...")

            if self.record_audio_path:
                future = asyncio.run_coroutine_threadsafe(
                    self.mcpClient.transcribe_audio_file(self.record_audio_path),
                    self.loop,
                )
                future.add_done_callback(self.handle_voice_result)

    def on_recorder_state_changed(self, _state):
        return

    def handle_voice_result(self, future):
        try:
            text = future.result()
        except Exception as e:
            text = f"语音识别失败: {e}"

        self.voice_signal.emit(text)

    def on_voice_transcribed(self, transcribed_text):
        if not transcribed_text:
            self.status_label.setText("语音识别失败，请重试")
            return

        if transcribed_text.startswith("语音识别失败") or transcribed_text.startswith("语音文件不存在"):
            self.status_label.setText(transcribed_text)
            self.add_to_history(transcribed_text, role="ai")
            return

        self.text_input.setPlainText(transcribed_text)
        self.status_label.setText("语音识别完成，正在发送请求...")
        self.send_message()

    def add_to_history(self, message, role="user"):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(2, 2, 2, 2)
        row_layout.setSpacing(8)

        sender = QLabel("你" if role == "user" else "AI")
        sender.setFixedWidth(28)
        sender.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        bubble = QFrame()
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 10, 12, 10)

        content_label = QLabel(message)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_label.setMaximumWidth(640)
        bubble_layout.addWidget(content_label)

        if role == "user":
            sender.setStyleSheet(
                "font-size: 11px; font-weight: 700; color: #4c6fff;"
                "background: #e8eeff; border: 1px solid #d2ddff; border-radius: 10px;"
            )
            bubble.setStyleSheet(
                "QFrame {background-color: #3f72ff; border: 1px solid #3568f2; border-radius: 16px;}"
            )
            content_label.setStyleSheet("color: white; font-size: 14px; line-height: 1.6;")
            row_layout.addStretch()
            row_layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
            row_layout.addWidget(sender)
        else:
            sender.setStyleSheet(
                "font-size: 11px; font-weight: 700; color: #596579;"
                "background: #f0f3f8; border: 1px solid #e0e6f0; border-radius: 10px;"
            )
            bubble.setStyleSheet(
                "QFrame {background-color: #f8fbff; border: 1px solid #d8e4f5; border-radius: 16px;}"
            )
            content_label.setStyleSheet("color: #263238; font-size: 14px; line-height: 1.6;")
            row_layout.addWidget(sender)
            row_layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
            row_layout.addStretch()

        self.history_layout.addWidget(row_widget)
        self.ensure_scroll_to_bottom()

    def ensure_scroll_to_bottom(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        try:
            if self.media_recorder and self.is_recording:
                self.media_recorder.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def clear_history(self):
        for i in reversed(range(self.history_layout.count())):
            widget = self.history_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        self.status_label.setText("历史记录已清空")
