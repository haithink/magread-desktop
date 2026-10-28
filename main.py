"""刊读 · Kandu Desktop — LLM 辅助英文外刊精读（Windows 桌面版）"""
import queue
import traceback
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from kandu import core_config as cfgmod
from kandu import core_parsers as store
from kandu import __version__
from kandu.ui_library import LibraryFrame
from kandu.ui_digest import DigestFrame
from kandu.ui_reader import ReaderFrame
from kandu.ui_settings import SettingsDialog


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"刊读 · Kandu Desktop v{__version__}")
        self.geometry("1180x800")
        self.minsize(900, 620)

        cfgmod.ensure_wordlists()
        self.settings = cfgmod.load_settings()
        self.q = queue.Queue()
        self.current = None
        self._erroring = False

        self.container = tk.Frame(self)
        self.container.pack(fill="both", expand=True)
        self.after(80, self._poll)
        self.show_library()

    # ---- 线程：worker 在后台跑，事件经队列回 UI 线程 ----
    def post(self, fn, *args):
        def run():
            try:
                fn(*args)
            except Exception as e:
                self.q.put(("error", str(e)))
        threading.Thread(target=run, daemon=True).start()

    def emit(self, kind, payload=None):
        self.q.put((kind, payload))

    def _poll(self):
        try:
            while True:
                try:
                    kind, payload = self.q.get_nowait()
                except queue.Empty:
                    break
                if kind == "error":
                    if not self._erroring:
                        self._erroring = True
                        try:
                            messagebox.showerror("出错了", str(payload), parent=self)
                        except Exception:
                            pass
                        self._erroring = False
                    continue
                handler = getattr(self.current, f"on_{kind}", None)
                if handler:
                    try:
                        handler(payload)
                    except Exception as e:
                        # 异常必须被隔离：否则 after 链中断，界面永久假死
                        self._handle_handler_error(e)
        finally:
            self.after(80, self._poll)

    # ---- 导航 ----
    def _swap(self, frame):
        if self.current is not None:
            self.current.destroy()
        self.current = frame
        frame.pack(fill="both", expand=True)

    def save_settings(self):
        cfgmod.save_settings(self.settings)

    def show_library(self):
        self._swap(LibraryFrame(self.container, self))

    def report_callback_exception(self, exc, val, tb):
        """UI 回调异常弹窗可见（--windowed 下无 stderr，静默吞掉会难排查）"""
        # 控件销毁后仍在派发的事件（双击打开页面瞬间）属无害噪音，忽略
        if isinstance(val, tk.TclError) and "invalid command name" in str(val):
            return
        detail = "".join(traceback.format_exception_only(exc, val))[-1500:]
        self._log_error(detail)
        try:
            messagebox.showerror("内部错误", detail, parent=self)
        except Exception:
            pass

    def _log_error(self, detail: str):
        try:
            import datetime
            with open(cfgmod.app_dir() / "error.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {detail}\n")
        except Exception:
            pass

    def _handle_handler_error(self, e: Exception):
        """事件处理器异常：销毁后派发的 TclError 静默；其余记录并弹窗（限频）。
        无论如何不让异常杀死 after 轮询链（否则界面永久假死）。"""
        if isinstance(e, tk.TclError) and "invalid command name" in str(e):
            return
        self._log_error(traceback.format_exc()[-2000:])
        try:
            messagebox.showerror("内部错误", str(e), parent=self)
        except Exception:
            pass

    def open_settings(self, on_saved=None):
        SettingsDialog(self, on_saved=on_saved)

    def open_digest(self, meta):
        self._swap(DigestFrame(self.container, self, meta))

    def open_reader(self, meta, article_index):
        self._swap(ReaderFrame(self.container, self, meta, article_index))


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
