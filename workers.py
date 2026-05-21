"""
workers.py — Background QThread worker for KB search and streaming API calls.
"""
import requests
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot


class StreamWorker(QObject):
    """
    Runs on a QThread. Receives work requests via slots (invoked from main thread)
    and emits results back via signals (received on main thread).

    Signal/Slot connections are automatically QueuedConnection when
    sender and receiver live in different threads.
    """

    # Signals emitted from worker thread → received on main thread
    chunk_received = pyqtSignal(str)         # single content delta
    stream_finished = pyqtSignal(str, str)   # full_answer, source_tag
    stream_error = pyqtSignal(str)           # error message
    status_changed = pyqtSignal(str)         # "searching" / "streaming" / "done" / "idle"

    def __init__(self, kb, api, parent=None):
        super().__init__(parent)
        self.kb = kb          # KnowledgeBase instance (read-only access)
        self.api = api        # DeepSeekClient instance
        self._cancelled = False

    def cancel(self):
        """Signal the worker to stop any in-flight operation."""
        self._cancelled = True

    @pyqtSlot(str, str, str, bool)
    def do_answer(self, question, kb_context, history_context, use_streaming):
        """
        Process a question through the API with auto-retry.
        use_streaming=True: emit chunk_received for each SSE delta.
        use_streaming=False: emit the full answer as one chunk.
        """
        self._cancelled = False
        self.status_changed.emit("streaming")

        for attempt in range(2):
            full_answer = ""
            source_tag = ""
            ok = False
            try:
                if use_streaming:
                    for delta in self.api.stream_query(
                        question=question, kb_context=kb_context,
                        history_context=history_context,
                    ):
                        if self._cancelled:
                            break
                        if delta:
                            full_answer += delta
                            self.chunk_received.emit(delta)
                else:
                    full_answer = self.api.query(
                        question=question, kb_context=kb_context,
                        history_context=history_context,
                    )
                    if not self._cancelled:
                        self.chunk_received.emit(full_answer)

                if self._cancelled:
                    self.stream_error.emit("已取消")
                    self.status_changed.emit("idle")
                    return

                self.stream_finished.emit(full_answer, source_tag)
                self.status_changed.emit("done")
                ok = True

            except requests.exceptions.Timeout:
                if attempt == 0:
                    continue
                self.stream_error.emit("请求超时，请检查网络连接")
            except requests.exceptions.HTTPError as e:
                self.stream_error.emit(f"API 错误 ({e.response.status_code})")
            except Exception as e:
                self.stream_error.emit(f"请求失败: {str(e)}")

            if not ok:
                self.status_changed.emit("idle")
                return
            break
