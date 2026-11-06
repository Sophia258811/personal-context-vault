"""Single-window Windows launcher: receiver, pairing and import worker."""
import ctypes
from ctypes import wintypes
import secrets
import sys
import threading
from contextlib import closing
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from .companion import create_server, desktop_ready_path
from .connectors import sync_connector
from .store import Store
from . import stream_media, stream_processing


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def pairing_token(root):
    path = root / 'pairing.bin'
    if path.exists():
        return protect(path.read_bytes(), decrypt=True).decode('ascii')
    token = secrets.token_urlsafe(32)
    with path.open('xb') as output:
        output.write(protect(token.encode('ascii')))
    return token


def configure(root):
    inbox = (root / 'browser-inbox').resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    store = Store(root / 'vault.db')
    try:
        store.init()
        matches = [c for c in store.list_connectors() if Path(c['input_path']).resolve() == inbox]
        if matches:
            connector = matches[0]
            if not connector['enabled'] or connector['source_status'] != 'active':
                raise ValueError('该来源已暂停或撤销，请明确恢复后再启动。')
            return connector['id']
        source = store.add_source(name='Browser companion', kind='browser',
            access_scope='User-enabled sites', legal_basis='user_consent')
        return store.add_connector(source_id=source, name='Browser inbox', platform='browser',
            format_name='json', input_path=inbox, access_mode='browser_assisted',
            content_scope='all_authorized', session_policy='ephemeral_browser_session')
    finally:
        store.close()


def main(quiet_start=False, minimized=False):
    window = tk.Tk()
    if minimized:
        window.iconify()
    window.title('PCV 伴随采集')
    window.geometry('540x320')
    root = Path(__file__).resolve().parents[1] / '.pcv'
    root.mkdir(exist_ok=True)
    status = tk.StringVar(value='正在启动…')
    stopped, pending, media_pending = threading.Event(), threading.Event(), threading.Event()
    report = {'text': '接收端已启动，等待页面。', 'media': '音画：等待授权片段。'}
    server = None

    def receive_media(payload):
        with closing(Store(root / 'vault.db')) as store:
            connector = store.get_connector(connector_id)
            if not connector['enabled']:
                raise stream_media.InactiveMediaSource('browser source is disabled')
            stream_media.ingest(store, connector['source_id'], payload)
        media_pending.set()

    try:
        token = pairing_token(root)
        server = create_server(root / 'browser-inbox', token, on_saved=lambda _: pending.set(),
                               ready_path=desktop_ready_path(root), on_media=receive_media)
        connector_id = configure(root)
        with closing(Store(root / 'vault.db')) as store:
            stream_media.initialize(store)
            stream_processing.initialize(store)
    except Exception as error:
        if server:
            server.server_close()
        if not quiet_start:
            messagebox.showerror('无法启动', '检查 8765 端口是否被旧接收端占用，以及来源是否有效。\n' + str(error))
        window.destroy()
        return

    def worker():
        store = Store(root / 'vault.db')
        try:
            while not stopped.is_set():
                pending.wait(15)
                pending.clear()
                if stopped.is_set():
                    break
                try:
                    result = sync_connector(store, connector_id)
                    report['text'] = f"接收中 · 本轮新增 {result['imported']} · 跳过 {result['skipped']} · 失败 {result['failed']}"
                except Exception:
                    report['text'] = '入库失败；原始文件仍在本机，请检查来源状态。'
        finally:
            store.close()

    def copy():
        window.clipboard_clear()
        window.clipboard_append(token)
        status.set('配对码已复制，首次粘贴到扩展；后续无需重复配对。')

    def media_worker():
        with closing(Store(root / 'vault.db')) as store:
            while not stopped.is_set():
                media_pending.wait(15)
                media_pending.clear()
                if stopped.is_set():
                    break
                try:
                    stream_processing.expire_processed_media(store)
                    if not store.get_connector(connector_id)['enabled']:
                        continue
                    while not stopped.is_set():
                        report['media'] = '音画：正在检查待处理片段。'
                        result = stream_processing.process_next(store)
                        if result is None:
                            report['media'] = '音画：已处理片段可通过 CLI 查询。'
                            break
                except Exception:
                    report['media'] = '音画处理暂时失败，稍后重试。'

    def stop():
        stopped.set()
        pending.set()
        media_pending.set()
        server.shutdown()
        server.server_close()
        importer.join(timeout=3)
        window.destroy()

    def refresh():
        status.set(report['text'] + '\n' + report['media'])
        window.after(5000, refresh)

    tk.Label(window, text='PCV 已就绪', font=('Microsoft YaHei UI', 18)).pack(pady=12)
    tk.Label(window, text='首次：加载扩展 → 复制配对码 → 扩展选择采集范围。\n以后：双击启动，已授权站点自动续采。\n最小化继续运行；关闭窗口停止接收。').pack(pady=6)
    tk.Button(window, text='复制首次配对码', command=copy).pack(pady=8)
    tk.Label(window, textvariable=status, wraplength=510).pack(pady=6)
    tk.Button(window, text='停止并退出', command=stop).pack()
    window.protocol('WM_DELETE_WINDOW', stop)
    importer = threading.Thread(target=worker, daemon=True)
    importer.start()
    threading.Thread(target=media_worker, daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pending.set()
    media_pending.set()
    refresh()
    window.mainloop()


if __name__ == '__main__':
    main(quiet_start='--quiet-start' in sys.argv[1:], minimized='--minimized' in sys.argv[1:])
