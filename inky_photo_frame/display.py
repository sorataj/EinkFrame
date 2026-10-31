"""Display management singleton with GPIO/SPI lifecycle handling."""

import logging
import sys
import threading
import atexit
import signal
import time as time_module
from functools import wraps

from inky_photo_frame.config import VERSION


class DisplayManager:
    """
    Singleton to manage Inky display with robust GPIO/SPI handling.
    Initializes once, cleans up only on exit.
    """
    _instance = None
    _display = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self):
        """Initialize display once at startup"""
        with self._lock:
            if self._initialized:
                return self._display

            logging.info(f'🚀 Inky Photo Frame v{VERSION}')
            logging.info('Initializing display...')

            try:
                from inky.auto import auto
                self._display = auto()
                self._initialized = True

                width, height = self._display.resolution
                logging.info(f'✅ Display initialized: {width}x{height}')

                # Register cleanup handlers. Replacing SIGTERM/SIGINT's
                # default handler means Python no longer exits on its own
                # when they're received - so we must call sys.exit()
                # ourselves, otherwise `systemctl stop/restart` (which sends
                # SIGTERM) would leave the process running until systemd's
                # SIGKILL timeout (default 90s) forcibly kills it instead.
                atexit.register(self.cleanup)

                def _handle_signal(signum, frame):
                    self.cleanup()
                    sys.exit(0)

                signal.signal(signal.SIGTERM, _handle_signal)
                signal.signal(signal.SIGINT, _handle_signal)

                return self._display

            except Exception as e:
                logging.error(f'❌ Failed to initialize display: {e}')
                raise

    def get_display(self):
        """Get the display instance (initializes if needed)"""
        if not self._initialized:
            return self.initialize()
        return self._display

    # Guards display.show() calls so a hardware hang (e.g. the panel's busy
    # signal never clearing) can't freeze the whole app forever - see
    # show_with_timeout below.
    _show_lock = threading.Lock()

    def show_with_timeout(self, timeout=90):
        """Call display.show() with a hard timeout.

        The Inky library's show() blocks waiting on the panel's hardware
        busy signal, normally for 20-40s. If that signal ever fails to
        clear (flaky cable, driver glitch, etc.), show() can hang forever -
        and since Python can't forcibly kill a thread, a plain blocking
        call would freeze the entire app's main loop (and with it, button
        presses and settings polling) permanently.

        This runs show() in a background thread and waits up to `timeout`
        seconds. If it doesn't finish in time, the stuck call is abandoned
        (left running in its own thread) and this returns False - letting
        the caller log an error and move on instead of hanging. A lock
        ensures at most one show() call (including an abandoned, still-
        running one) touches the hardware at a time; if a previous call is
        still stuck when a new one is attempted, the new one is skipped
        immediately rather than queuing behind it indefinitely.
        """
        if not self._show_lock.acquire(blocking=False):
            logging.warning('⚠️ Display still mid-refresh from a previous call '
                           '(possibly hung) - skipping this update')
            return False

        result = {'error': None}

        def worker():
            try:
                self._display.show()
            except Exception as e:
                result['error'] = e
            finally:
                self._show_lock.release()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            logging.error(f'❌ display.show() did not finish within {timeout}s - '
                         'hardware may be hung. Abandoning this call; further '
                         'updates will be skipped until it completes.')
            return False

        if result['error']:
            raise result['error']
        return True

    def cleanup(self):
        """Cleanup display resources on exit"""
        with self._lock:
            if self._initialized and self._display:
                try:
                    if hasattr(self._display, '_spi'):
                        self._display._spi.close()
                    logging.info('🧹 Display cleaned up properly')
                except Exception as e:
                    logging.warning(f'Cleanup warning: {e}')
                finally:
                    self._initialized = False
                    self._display = None


def retry_on_error(max_attempts=3, delay=1, backoff=2):
    """
    Decorator to retry operations on GPIO/SPI errors
    Uses exponential backoff for resilience
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_msg = str(e).lower()
                    # Check if it's a recoverable error
                    is_recoverable = any(x in error_msg for x in [
                        'gpio', 'spi', 'pins', 'transport', 'endpoint', 'busy'
                    ])

                    if is_recoverable and attempt < max_attempts:
                        wait_time = delay * (backoff ** (attempt - 1))
                        logging.warning(f'⚠️ Attempt {attempt}/{max_attempts} failed: {e}')
                        logging.info(f'Retrying in {wait_time}s...')
                        time_module.sleep(wait_time)
                    else:
                        logging.error(f'❌ Operation failed after {attempt} attempts: {e}')
                        raise
            return None
        return wrapper
    return decorator
