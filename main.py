#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Interview Helper - 面试辅助工具
透明悬浮窗，知识库优先检索 + DeepSeek API 补充
"""

import sys
import os
import json
import re
import math
import time
from collections import Counter

import requests
import keyboard as _kb
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextBrowser,
    QLineEdit, QLabel, QSystemTrayIcon, QMenu
)
from PyQt5.QtCore import (
    Qt, QThread, QObject, pyqtSignal, QTimer
)
from PyQt5.QtGui import (
    QColor, QFont, QTextCharFormat, QTextCursor, QIcon, QPixmap, QPainter, QPalette
)

from workers import StreamWorker

# ═══════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════

def _config_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'config.json')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

def load_config():
    dft = {
        "api_key": "",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "kb_path": "",
        "window_width": 620,
        "window_height": 420,
        "max_history": 3,
        "window_x": -1,
        "window_y": -1,
        "opacity": 0.30,
        "auto_clear_timeout_ms": 90000,
        "font_family": "Microsoft YaHei",
        "font_size": 13,
        "streaming_enabled": True,
        "temperature": 0.4,
        "max_tokens": 1000,
        "kb_search_top_k": 5,
        "kb_context_max_chars": 2500,
        "mode": "实时辅助",
        "persist_history": False,
        "model": "deepseek-chat",
        "scroll_speed_ms": 50,
    }
    p = _config_path()
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            # Backward compat: text_opacity → opacity
            if "opacity" not in loaded and "text_opacity" in loaded:
                loaded["opacity"] = loaded["text_opacity"]
            dft.update(loaded)
        except Exception:
            pass
    return dft

CFG = load_config()

# ═══════════════════════════════════════════════
# Knowledge Base Engine
# ═══════════════════════════════════════════════

class KnowledgeBase:
    def __init__(self, kb_path):
        self.kb_path = kb_path
        self.chunks = []       # [{source, header, content}]
        self.idf = {}          # word → idf weight
        self.ready = False
        if kb_path and os.path.isdir(kb_path):
            self._load()

    # ── loading ──────────────────────────────

    def _load(self):
        all_texts = []
        for fname in sorted(os.listdir(self.kb_path)):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(self.kb_path, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue
            sections = self._parse_sections(fname.replace('.md', ''), content)
            self.chunks.extend(sections)
            all_texts.extend(s['content'] for s in sections)
        self._compute_idf(all_texts)
        self.ready = True
        print(f"[KB] Loaded {len(self.chunks)} chunks from {len(all_texts)} sections")

    @staticmethod
    def _parse_sections(source, content):
        sections = []
        lines = content.split('\n')
        cur_header = "概述"
        cur_body = []
        for line in lines:
            if line.startswith('## ') or line.startswith('### '):
                if cur_body:
                    text = '\n'.join(cur_body).strip()
                    if text:
                        sections.append(dict(source=source, header=cur_header, content=text))
                cur_header = line.lstrip('#').strip()
                cur_body = []
            else:
                cur_body.append(line)
        if cur_body:
            text = '\n'.join(cur_body).strip()
            if text:
                sections.append(dict(source=source, header=cur_header, content=text))
        return sections

    # ── tokenisation ─────────────────────────

    @staticmethod
    def _tokenize(text):
        text = re.sub(r'([一-鿿])', r' \1 ', text)
        return re.findall(r'[一-鿿\w]+', text.lower())

    def _compute_idf(self, texts):
        n = len(texts)
        df = Counter()
        for t in texts:
            for w in set(self._tokenize(t)):
                df[w] += 1
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}

    # ── search ───────────────────────────────

    def search(self, query, top_k=5):
        if not self.ready or not self.chunks:
            return []
        qtokens = self._tokenize(query)
        scored = []
        for ch in self.chunks:
            ctokens = self._tokenize(ch['content'])
            ccnt = Counter(ctokens)
            score = 0.0
            for qt in qtokens:
                if qt in self.idf:
                    tf = ccnt.get(qt, 0) / max(len(ctokens), 1)
                    score += tf * self.idf[qt]
            # header boost
            if any(qt in self._tokenize(ch['header']) for qt in qtokens):
                score *= 2
            if score > 0:
                scored.append((score, ch))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            dict(source=s['source'], header=s['header'], content=s['content'][:1200], score=round(sc, 4))
            for sc, s in scored[:top_k]
        ]

    def format_context(self, results, max_chars=2500):
        parts = []
        total = 0
        for r in results:
            snippet = f"[{r['source']} - {r['header']}]\n{r['content']}"
            if total + len(snippet) > max_chars:
                remain = max_chars - total
                if remain > 200:
                    parts.append(snippet[:remain] + '…')
                break
            parts.append(snippet)
            total += len(snippet)
        return '\n\n'.join(parts)


# ═══════════════════════════════════════════════
# Conversation History
# ═══════════════════════════════════════════════

class ConversationHistory:
    """Manages conversation turns with smart trimming and optional persistence."""

    def __init__(self, max_turns=3, persist_path=None):
        self.turns = []
        self.max_turns = max_turns
        self.persist_path = persist_path
        self._load()

    def add_turn(self, question, answer, source_tag="", kb_results=None):
        self.turns.append({
            "role": "user", "content": question, "timestamp": time.time(),
        })
        self.turns.append({
            "role": "assistant", "content": answer,
            "source_tag": source_tag, "kb_results": kb_results,
            "timestamp": time.time(),
        })
        self._trim()
        self._save()

    @property
    def last_pair(self):
        if len(self.turns) >= 2:
            return self.turns[-2]["content"], self.turns[-1]["content"]
        return None, None

    def get_last_turn_context(self, max_chars=500):
        q, a = self.last_pair
        if not q or not a or len(a) >= max_chars:
            return ""
        return f"Q: {q[:100]}\nA: {a[:200]}"

    def _trim(self):
        max_entries = self.max_turns * 2
        while len(self.turns) > max_entries:
            self.turns.pop(0)

    def clear(self):
        self.turns.clear()
        self._save()

    def _save(self):
        if not self.persist_path:
            return
        try:
            with open(self.persist_path, 'w', encoding='utf-8') as f:
                json.dump({"turns": self.turns}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load(self):
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.turns = data.get("turns", [])
        except Exception:
            self.turns = []


# ═══════════════════════════════════════════════
# DeepSeek API Client
# ═══════════════════════════════════════════════

class DeepSeekClient:
    def __init__(self, api_key, api_url):
        self.api_key = api_key
        self.api_url = api_url

    def _build_messages(self, question, kb_context="", history_context=""):
        """Shared message builder for both blocking and streaming paths."""
        system = (
            "你是面试辅助助手。回答规则：\n"
            "1. 结论先行：第一句话直接回答核心问题\n"
            "2. 简洁精炼：控制在 150-200 字以内，在合适位置加入英文技术术语（如 agent, RAG, fine-tuning, prompt 等）\n"
            "3. 自然口语化：像专业人士当场回答，不要序号和书面语\n"
            "4. 参考优先：如果提供了知识库资料，必须基于资料回答"
        )
        msgs = [{"role": "system", "content": system}]
        user_content = question
        if kb_context:
            user_content = f"知识库资料：\n{kb_context}\n\n请基于以上资料回答：{question}"
        if history_context:
            user_content += f"\n\n注意：上轮对话为「{history_context}」，如果是追问请延续上下文"
        msgs.append({"role": "user", "content": user_content})
        return msgs

    def stream_query(self, question, kb_context="", history_context=""):
        """
        Generator that yields content deltas as they arrive via SSE.
        Used by StreamWorker in background thread.
        """
        if not self.api_key:
            yield "API 密钥未配置"
            return

        msgs = self._build_messages(question, kb_context, history_context)

        resp = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json={
                "model": CFG.get('model', 'deepseek-chat'),
                "messages": msgs,
                "temperature": CFG.get('temperature', 0.4),
                "max_tokens": CFG.get('max_tokens', 600),
                "stream": True,
            },
            stream=True,
            timeout=25,
        )
        resp.raise_for_status()

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if raw_line.startswith("data: "):
                payload = raw_line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
                except json.JSONDecodeError:
                    continue

    def query(self, question, kb_context="", history_context=""):
        """Blocking call (kept for backward compat / non-streaming mode)."""
        if not self.api_key:
            return "API 密钥未配置，请在 config.json 中设置 api_key"

        msgs = self._build_messages(question, kb_context, history_context)

        try:
            resp = requests.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": CFG.get('model', 'deepseek-chat'),
                    "messages": msgs,
                    "temperature": CFG.get('temperature', 0.4),
                    "max_tokens": CFG.get('max_tokens', 600),
                    "stream": False,
                },
                timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']
        except requests.exceptions.Timeout:
            return "请求超时，请检查网络连接"
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 401:
                return "API Key 无效，请检查 config.json"
            return f"API 错误 ({resp.status_code})"
        except Exception as e:
            return f"请求失败: {str(e)}"


# ═══════════════════════════════════════════════
# Mode System
# ═══════════════════════════════════════════════

class BaseMode:
    """
    Abstract base for all assistant modes.
    Each mode defines its own behavior for submission, activation, and display.
    """
    name = "base"
    description = ""

    def __init__(self, overlay):
        self.overlay = overlay

    def on_activate(self):
        """Called when this mode becomes active."""
        pass

    def on_deactivate(self):
        """Called when switching away from this mode."""
        pass

    def on_submit(self, question):
        """Process a user submission. Must be overridden."""
        raise NotImplementedError

    def get_input_placeholder(self):
        """Placeholder text for the input field."""
        return "输入问题… (Enter)"


class RealtimeAssistMode(BaseMode):
    name = "实时辅助"
    description = "知识库检索 + DeepSeek API 实时回答"

    def __init__(self, overlay, kb, api):
        super().__init__(overlay)
        self.kb = kb
        self.api = api

    def get_input_placeholder(self):
        kb_count = 0
        if CFG.get('kb_path') and os.path.isdir(CFG['kb_path']):
            kb_count = sum(1 for f in os.listdir(CFG['kb_path']) if f.endswith('.md'))
        return f"输入问题… (Enter)  KB: {'%d篇' % kb_count if kb_count else '未加载'}"

    def on_submit(self, question):
        o = self.overlay  # shorthand

        # Cancel any in-flight stream
        if o._streaming_active:
            o._worker.cancel()
            o._streaming_active = False
            o._current_answer_buffer = ""

        # Add separator between turns
        if o._turn_count > 0:
            o._append("─" * 40 + "\n", o._sep_fmt)
        o._turn_count += 1

        # Append question to display
        o._append(f"Q: {question}\n", o._q_fmt)
        o._current_question = question
        o._current_results = []
        o._current_answer_buffer = ""

        # KB search (fast, stays on main thread)
        results = self.kb.search(
            question,
            top_k=CFG.get('kb_search_top_k', 5)
        ) if self.kb.ready else []
        o._current_results = results
        context = self.kb.format_context(
            results,
            max_chars=CFG.get('kb_context_max_chars', 2500)
        ) if results else ""

        # Build history context
        history_ctx = o._history_mgr.get_last_turn_context(max_chars=500)

        # Start streaming in background thread
        use_streaming = CFG.get('streaming_enabled', True)
        o._request_stream.emit(question, context, history_ctx, use_streaming)


# ═══════════════════════════════════════════════
# Transparent Overlay Window
# ═══════════════════════════════════════════════

class OverlayWindow(QWidget):
    """Transparent overlay with streaming conversation display."""

    # Signal to invoke work in the background thread
    _request_stream = pyqtSignal(str, str, str, bool)  # question, kb_context, history_context, use_streaming

    def __init__(self, kb, api):
        super().__init__()
        self.kb = kb
        self.api = api
        self._dragging = False
        self._drag_pos = None
        self._opacity = CFG.get('opacity', 0.30)

        # Conversation history
        persist = None
        if CFG.get('persist_history', False):
            persist = os.path.join(os.path.dirname(_config_path()), "history.json")
        self._history_mgr = ConversationHistory(
            max_turns=CFG.get('max_history', 3),
            persist_path=persist,
        )
        self._current_question = ""
        self._current_results = []
        self._streaming_active = False
        self._current_answer_buffer = ""
        self._loading_frames = [".", "..", "...", "...."]
        self._loading_idx = 0
        self._loading_status_text = ""
        self._turn_count = 0
        self._modes = {}
        self._current_mode = None

        self._build_ui()

        # Register and activate default mode
        self.register_mode(RealtimeAssistMode(self, kb, api))
        default_mode = CFG.get('mode', '实时辅助')
        self.activate_mode(default_mode)
        self._position_window()
        self._setup_timer()
        self._setup_worker()

    # ── UI ────────────────────────────────────

    def _build_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Background opacity from config (0.0 = fully transparent, 1.0 = fully opaque)
        self._bg_alpha = max(1, min(255, int(255 * self._opacity)))

        w = CFG.get('window_width', 620)
        h = CFG.get('window_height', 420)
        self.resize(w, h)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Conversation display — fully transparent
        self.display = QTextBrowser(self)
        self.display.setFrameShape(0)
        self.display.setReadOnly(True)
        self.display.setAutoFillBackground(False)
        self.display.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.display.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.display.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
            }
            QTextBrowser QScrollBar:vertical {
                width: 6px;
                background: transparent;
            }
            QTextBrowser QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 50);
                border-radius: 3px;
                min-height: 20px;
            }
            QTextBrowser QScrollBar::add-line:vertical,
            QTextBrowser QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QTextBrowser QScrollBar::add-page:vertical,
            QTextBrowser QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        pal = self.display.palette()
        pal.setColor(QPalette.Base, Qt.transparent)
        pal.setColor(QPalette.Window, Qt.transparent)
        self.display.setPalette(pal)

        # Text uses high fixed alpha for readability regardless of config opacity
        self._text_opacity = 0.90
        self._alpha = max(10, min(255, int(255 * self._text_opacity)))
        self._font = QFont(
            CFG.get('font_family', 'Microsoft YaHei'),
            CFG.get('font_size', 13)
        )

        self._q_fmt = QTextCharFormat()
        self._q_fmt.setForeground(QColor(180, 215, 255, self._alpha))
        self._q_fmt.setFontWeight(QFont.Bold)
        self._q_fmt.setFont(self._font)

        self._a_fmt = QTextCharFormat()
        self._a_fmt.setForeground(QColor(255, 255, 255, self._alpha))
        self._a_fmt.setFont(self._font)

        self._source_fmt = QTextCharFormat()
        self._source_fmt.setForeground(QColor(180, 180, 180, self._alpha // 2))
        smaller = QFont(self._font.family(), max(8, self._font.pointSize() - 2))
        self._source_fmt.setFont(smaller)

        self._sep_fmt = QTextCharFormat()
        self._sep_fmt.setForeground(QColor(80, 80, 80, self._alpha // 2))

        self.display.setFont(self._font)
        self._show_welcome()
        layout.addWidget(self.display, 1)

        # Loading indicator
        self.loading_label = QLabel("", self)
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("""
            QLabel {
                color: rgba(180, 180, 180, 180);
                font-size: 13px;
                background: transparent;
                padding: 4px;
            }
        """)
        self.loading_label.hide()
        layout.addWidget(self.loading_label)

        # Input field
        kb_count = 0
        if CFG.get('kb_path') and os.path.isdir(CFG['kb_path']):
            kb_count = sum(1 for f in os.listdir(CFG['kb_path']) if f.endswith('.md'))
        self.input_field = QLineEdit(self)
        self.input_field.setPlaceholderText(
            f"输入问题… (Enter)  KB: {'%d篇' % kb_count if kb_count else '未加载'}"
        )
        iala = 200  # text alpha — keep readable
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(20, 22, 30, 12);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 5px;
                color: rgba(255, 255, 255, {iala});
                padding: 8px 10px;
                font-size: 13px;
                font-family: "Microsoft YaHei", sans-serif;
            }}
            QLineEdit:focus {{
                border-color: rgba(100, 165, 255, 25);
            }}
        """)
        self.input_field.returnPressed.connect(self._on_submit)
        layout.addWidget(self.input_field)


    def _position_window(self):
        scr = QApplication.primaryScreen().geometry()
        w = self.width()
        h = self.height()
        sx = CFG.get('window_x', -1)
        sy = CFG.get('window_y', -1)
        if 0 <= sx <= scr.width() - 100 and 0 <= sy <= scr.height() - 100:
            self.move(sx, sy)
        else:
            self.move((scr.width() - w) // 2, 40)

    def _setup_timer(self):
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._auto_clear)

        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._animate_loading)

    # ── worker thread ──────────────────────────

    def _setup_worker(self):
        self._worker_thread = QThread(self)
        self._worker = StreamWorker(self.kb, self.api)
        self._worker.moveToThread(self._worker_thread)

        # Connect request signal → worker slot (auto-queued, cross-thread)
        self._request_stream.connect(self._worker.do_answer)

        # Connect worker signals → UI slots (auto-queued, cross-thread)
        self._worker.chunk_received.connect(self._on_chunk_received)
        self._worker.stream_finished.connect(self._on_answer_complete)
        self._worker.stream_error.connect(self._on_stream_error)
        self._worker.status_changed.connect(self._on_worker_status)

        self._worker_thread.start()

    def cleanup_worker(self):
        """Call on app shutdown."""
        self._worker.cancel()
        self._worker_thread.quit()
        self._worker_thread.wait(3000)

    # ── mode system ────────────────────────────

    def register_mode(self, mode):
        self._modes[mode.name] = mode

    def activate_mode(self, mode_name):
        if self._current_mode:
            self._modes[self._current_mode].on_deactivate()
        if mode_name not in self._modes and self._modes:
            # Fallback to first registered mode if name not found
            mode_name = next(iter(self._modes))
        if mode_name in self._modes:
            self._current_mode = mode_name
            self._modes[mode_name].on_activate()
            self.input_field.setPlaceholderText(
                self._modes[mode_name].get_input_placeholder()
            )

    # ── public API ────────────────────────────

    def toggle_input(self):
        """Focus the input field (always visible)."""
        self.input_field.setFocus()
        self.input_field.activateWindow()
        self.raise_()

    def clear_display(self):
        """Manually clear display and history."""
        if self._streaming_active:
            self._worker.cancel()
            self._streaming_active = False
            self._current_answer_buffer = ""
        self._auto_clear()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.input_field.setFocus()
            self.input_field.activateWindow()

    # ── input ─────────────────────────────────

    def _on_submit(self):
        question = self.input_field.text().strip()
        if not question:
            return
        self.input_field.clear()
        self._clear_timer.stop()

        # Delegate to active mode
        if self._current_mode and self._current_mode in self._modes:
            self._modes[self._current_mode].on_submit(question)

    def _on_chunk_received(self, chunk):
        """Append streaming content delta to display."""
        if not self._streaming_active:
            self._append("A: ", self._a_fmt)
            self._streaming_active = True
        self._current_answer_buffer += chunk
        self._append(chunk, self._a_fmt)
        self._auto_scroll()

    def _on_answer_complete(self, full_answer, _ignored_source_tag):
        """Streaming complete — finalize the answer."""
        source_tag = ""
        if self._current_results:
            sources = list(dict.fromkeys(r['source'] for r in self._current_results[:3]))
            if sources:
                source_tag = "  [" + ", ".join(sources[:2]) + "]"
        if source_tag:
            self._append(source_tag, self._source_fmt)
        self._append("\n", self._a_fmt)

        # Save to history
        self._history_mgr.add_turn(
            self._current_question, full_answer,
            source_tag=source_tag, kb_results=self._current_results
        )

        # Copy answer to clipboard
        try:
            QApplication.clipboard().setText(full_answer)
        except Exception:
            pass

        self._streaming_active = False
        self._current_answer_buffer = ""

        timeout = CFG.get('auto_clear_timeout_ms', 90000)
        if timeout > 0:
            self._clear_timer.start(timeout)

        self._auto_scroll()

    def _on_stream_error(self, error_msg):
        """Streaming failed — show error."""
        if not self._streaming_active:
            self._append("A: ", self._a_fmt)
        self._append(f"[错误] {error_msg}\n\n", self._a_fmt)
        self._streaming_active = False
        self._current_answer_buffer = ""
        self._clear_timer.stop()
        self.loading_label.hide()
        self._loading_timer.stop()

    def _on_worker_status(self, status):
        """Update loading indicator based on worker status."""
        if status == "streaming":
            self._loading_status_text = "生成回答"
            self._loading_idx = 0
            self.loading_label.show()
            self._loading_timer.start(500)
        else:
            self._loading_timer.stop()
            self.loading_label.hide()

    def _animate_loading(self):
        frame = self._loading_frames[self._loading_idx % len(self._loading_frames)]
        self.loading_label.setText(f"{self._loading_status_text}{frame}")
        self._loading_idx += 1

    def _append(self, text, fmt):
        cur = self.display.textCursor()
        cur.movePosition(cur.End)
        cur.insertText(text, fmt)
        self.display.setTextCursor(cur)

    def _auto_scroll(self):
        sb = self.display.verticalScrollBar()
        if sb.value() >= sb.maximum() - 20:
            QTimer.singleShot(CFG.get('scroll_speed_ms', 50),
                             lambda: sb.setValue(sb.maximum()))

    def _auto_clear(self):
        self.display.clear()
        self._history_mgr.clear()
        self._turn_count = 0
        self._show_welcome()

    def _show_welcome(self):
        """Insert welcome/instruction text at the top of the display."""
        welcome_fmt = QTextCharFormat()
        welcome_fmt.setForeground(QColor(180, 180, 180, self._alpha))
        welcome_fmt.setFont(QFont(self._font.family(), max(10, self._font.pointSize() - 1)))
        small_fmt = QTextCharFormat()
        small_fmt.setForeground(QColor(140, 140, 140, self._alpha))
        small_fmt.setFont(QFont(self._font.family(), max(9, self._font.pointSize() - 2)))
        cur = self.display.textCursor()
        cur.movePosition(cur.Start)
        cur.insertText("Interview Helper 已启动\n", welcome_fmt)
        kb_count = 0
        if CFG.get('kb_path') and os.path.isdir(CFG['kb_path']):
            kb_count = sum(1 for f in os.listdir(CFG['kb_path']) if f.endswith('.md'))
        api_status = "✓" if CFG.get('api_key') else "✗ 未配置"
        kb_status = f"{kb_count} 篇" if kb_count else "未加载"
        cur.insertText(
            f"API: {api_status}  |  知识库: {kb_status}\n\n", small_fmt)
        cur.insertText("Alt+Q        切换输入框\n", welcome_fmt)
        cur.insertText("Ctrl+Shift+H  显示/隐藏\n", welcome_fmt)
        cur.insertText("Alt+Shift+C  清除对话\n", welcome_fmt)
        cur.insertText("Ctrl+Shift+Q  退出\n", welcome_fmt)

    # ── background painting ─────────────────────

    def paintEvent(self, event):
        """Draw semi-transparent black background for overlay effect."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, self._bg_alpha))

    # ── mouse drag ────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging and e.buttons() == Qt.LeftButton:
            self.move(e.globalPos() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._dragging = False
        # Save window position to config
        pos = self.pos()
        CFG['window_x'] = pos.x()
        CFG['window_y'] = pos.y()
        e.accept()


# ═══════════════════════════════════════════════
# Windows Hotkey (keyboard module)
# ═══════════════════════════════════════════════


class HotkeySignals(QObject):
    input_triggered = pyqtSignal()
    hide_triggered = pyqtSignal()
    quit_triggered = pyqtSignal()
    clear_triggered = pyqtSignal()


_hotkey_registrations = []


def setup_hotkeys(sig):
    """Register global hotkeys using keyboard module (background thread)."""
    global _hotkey_registrations
    _kb.unhook_all()
    _hotkey_registrations = [
        _kb.add_hotkey('alt+q', sig.input_triggered.emit),
        _kb.add_hotkey('ctrl+shift+h', sig.hide_triggered.emit),
        _kb.add_hotkey('ctrl+shift+q', sig.quit_triggered.emit),
        _kb.add_hotkey('alt+shift+c', sig.clear_triggered.emit),
    ]


def remove_hotkeys():
    """Remove all global hotkey hooks."""
    _kb.unhook_all()


# ═══════════════════════════════════════════════
# Save config on exit
# ═══════════════════════════════════════════════

def save_config():
    p = _config_path()
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(CFG, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ═══════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════

def main():
    QApplication.setAttribute(0x101)  # AA_EnableHighDpiScaling
    QApplication.setAttribute(0x103)  # AA_UseHighDpiPixmaps

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Components
    kb = KnowledgeBase(CFG.get('kb_path', ''))
    api = DeepSeekClient(CFG.get('api_key', ''), CFG.get('api_url', ''))
    overlay = OverlayWindow(kb, api)
    overlay.show()

    # Hotkeys
    sig = HotkeySignals()
    sig.input_triggered.connect(overlay.toggle_input)
    sig.hide_triggered.connect(overlay.toggle_visibility)
    sig.quit_triggered.connect(app.quit)
    sig.clear_triggered.connect(overlay.clear_display)
    setup_hotkeys(sig)

    # System tray
    pix = QPixmap(16, 16)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setPen(QColor(180, 180, 180, 160))
    p.setBrush(QColor(180, 180, 180, 60))
    p.drawRoundedRect(1, 1, 14, 14, 3, 3)
    p.end()
    tray_icon = QSystemTrayIcon(QIcon(pix), app)
    tray_icon.setToolTip("Interview Helper\nAlt+Q 输入 | Alt+Shift+C 清除 | Ctrl+Shift+H 隐藏")
    menu = QMenu()
    show_act = menu.addAction("显示 / 隐藏")
    show_act.triggered.connect(overlay.toggle_visibility)
    menu.addSeparator()
    quit_act = menu.addAction("退出")
    quit_act.triggered.connect(app.quit)
    tray_icon.setContextMenu(menu)
    tray_icon.show()

    # Clean exit
    app.aboutToQuit.connect(lambda: (overlay.cleanup_worker(), remove_hotkeys(), save_config()))

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
