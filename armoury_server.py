from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import viewer


APP_DIR = Path(r"C:\dev\Armoury")
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "server.log"
ARCHIVE_DIR = APP_DIR / "wow_archive"
ICON_FILE = APP_DIR / "armoury.ico"
PORT = 5000
HOST = "0.0.0.0"
MUTEX_NAME = "Local\\ArmouryServerTrayApplication"
ERROR_ALREADY_EXISTS = 183

logger = logging.getLogger("armoury_server")
_lifecycle_lock = threading.RLock()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None


class ArmouryHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def handle_error(self, request: object, client_address: tuple[str, int]) -> None:
        logger.exception("Unhandled request error from %s", client_address[0])


def _request_handler() -> type[BaseHTTPRequestHandler]:
    base_handler = viewer.create_handler(ARCHIVE_DIR)

    class LoggingRequestHandler(base_handler):
        def setup(self) -> None:
            self.request.settimeout(15)
            super().setup()

        def log_message(self, message_format: str, *args: object) -> None:
            logger.info(
                "%s - %s", self.address_string(), message_format % args
            )

    return LoggingRequestHandler


def start_server() -> None:
    global _server, _server_thread

    with _lifecycle_lock:
        if _server is not None:
            raise RuntimeError("The web server is already running.")

        viewer.STATIC_DIR = APP_DIR / "viewer"
        logger.info("Starting web server on %s:%d", HOST, PORT)
        server = ArmouryHTTPServer(
            (HOST, PORT),
            _request_handler(),
        )
        thread = threading.Thread(
            target=_serve,
            args=(server,),
            name="ArmouryWebServer",
            daemon=True,
        )
        _server = server
        _server_thread = thread
        try:
            thread.start()
        except Exception:
            _server = None
            _server_thread = None
            server.server_close()
            logger.exception("Could not start the web server thread")
            raise


def _serve(server: ThreadingHTTPServer) -> None:
    try:
        server.serve_forever(poll_interval=0.25)
    except Exception:
        logger.exception("The web server stopped unexpectedly")


def stop_server() -> None:
    global _server, _server_thread

    with _lifecycle_lock:
        server = _server
        thread = _server_thread
        if server is None:
            return

        logger.info("Stopping web server")
        shutdown_failed = False

        try:
            server.shutdown()
        except Exception:
            shutdown_failed = True
            logger.exception("Error while stopping the web server")

        try:
            server.server_close()
        except Exception:
            shutdown_failed = True
            logger.exception("Error while closing the web server socket")

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
            if thread.is_alive():
                shutdown_failed = True
                logger.error("Web server thread did not stop within 10 seconds")

        if shutdown_failed:
            raise RuntimeError("The web server did not shut down cleanly.")
        _server = None
        _server_thread = None
        logger.info("Web server stopped")


def restart_server(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    logger.info("Server restart requested")
    try:
        stop_server()
        start_server()
    except Exception:
        logger.exception("Could not restart the web server")
        _show_message("The server could not be restarted. See the log for details.")
    else:
        logger.info("Server restart completed")


def _open_dashboard(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    try:
        if not webbrowser.open("http://localhost:5000"):
            raise RuntimeError("No browser accepted the dashboard URL.")
        logger.info("Opened dashboard")
    except Exception:
        logger.exception("Could not open the dashboard")
        _show_message("The dashboard could not be opened.")


def _open_data_folder(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    _open_folder(APP_DIR)


def _open_logs(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    _open_folder(LOG_DIR)


def _open_folder(path: Path) -> None:
    try:
        os.startfile(str(path))
        logger.info("Opened folder: %s", path)
    except Exception:
        logger.exception("Could not open folder: %s", path)
        _show_message(f"The folder could not be opened:\n{path}")


def _exit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    logger.info("Exit requested from tray menu")
    icon.stop()


def _create_fallback_icon() -> Image.Image:
    image = Image.new("RGB", (64, 64), (25, 34, 52))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=(39, 55, 81), outline=(222, 174, 76), width=3)
    draw.polygon(
        ((32, 13), (49, 49), (40, 49), (36, 39), (27, 39), (23, 49), (15, 49)),
        fill=(238, 196, 104),
    )
    draw.polygon(((29, 33), (34, 33), (32, 27)), fill=(39, 55, 81))
    return image


def _load_icon_image() -> Image.Image:
    bundled_icon = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
    ) / ICON_FILE.name
    for icon_path in dict.fromkeys((bundled_icon, ICON_FILE)):
        if not icon_path.is_file():
            continue
        try:
            with Image.open(icon_path) as image:
                return image.convert("RGBA")
        except OSError:
            logger.exception(
                "Could not load custom icon %s; trying another location",
                icon_path,
            )
    logger.info("Custom icon not found; using generated fallback icon")
    return _create_fallback_icon()


def _create_tray_icon() -> pystray.Icon:
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", _open_dashboard),
        pystray.MenuItem("Open Data Folder", _open_data_folder),
        pystray.MenuItem("Restart Server", restart_server),
        pystray.MenuItem("View Logs", _open_logs),
        pystray.MenuItem("Exit", _exit),
    )
    return pystray.Icon(
        "armoury-server",
        _load_icon_image(),
        "Armoury Server",
        menu,
    )


def _show_message(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(
        None,
        message,
        "Armoury Server",
        0x00000000 | 0x00000010,
    )


def _acquire_single_instance_mutex() -> tuple[int, bool]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def _close_mutex_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False


def main() -> None:
    mutex_handle: int | None = None
    logging_configured = False
    try:
        mutex_handle, already_running = _acquire_single_instance_mutex()
        if already_running:
            _show_message("Armoury Server is already running.")
            return

        _configure_logging()
        logging_configured = True
        logger.info("Armoury Server starting")
        try:
            start_server()
            _create_tray_icon().run()
        except Exception:
            logger.exception("Armoury Server encountered an error")
            _show_message("Armoury Server encountered an error. See the log for details.")
        finally:
            logger.info("Armoury Server shutting down")
            try:
                stop_server()
            except Exception:
                logger.exception("Armoury Server did not shut down cleanly")
            logger.info("Armoury Server stopped")
    except Exception:
        if logging_configured:
            logger.exception("Armoury Server could not start")
        else:
            _show_message("Armoury Server could not start. Check folder permissions.")
    finally:
        if mutex_handle:
            try:
                _close_mutex_handle(mutex_handle)
            except OSError:
                logger.exception("Could not release the single-instance mutex")
        if logging_configured:
            logging.shutdown()


if __name__ == "__main__":
    main()
