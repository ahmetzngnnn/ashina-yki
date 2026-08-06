"""
joystick_worker.py — Gamepad İşleyicisi
pygame joystick alt sistemi ile 50 Hz polling.
Eksen değerleri [-1.0, 1.0] → [-1000, 1000] MAVLink aralığına ölçeklenir.
"""
import logging
import time
from PyQt6.QtCore import QThread, pyqtSignal

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    logging.warning("pygame kurulu değil — Joystick devre dışı")

logger = logging.getLogger(__name__)

POLL_HZ = 50


class JoystickWorker(QThread):
    # x, y, z, r (yaw) değerleri -1000..1000
    joystick_updated = pyqtSignal(int, int, int, int, int)  # x, y, z, r, buttons
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._joystick_index: int = 0
        self.heartbeat_counter: int = 0

    def set_joystick_index(self, idx: int) -> None:
        self._joystick_index = idx

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                self._run_loop()
            except Exception as exc:
                logger.error("JoystickWorker exception: %s", exc)
                self.error_occurred.emit(str(exc))
                time.sleep(2)

    def _run_loop(self) -> None:
        if not PYGAME_AVAILABLE:
            self.heartbeat_counter += 1
            time.sleep(1)
            return

        # pygame.init() tüm alt sistemleri başlatır; joystick.init() tek başına
        # bazı platformlarda event queue olmadan çalışmaz
        if not pygame.get_init():
            pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            logger.info("Joystick bulunamadı, bekleniyor...")
            self.heartbeat_counter += 1
            time.sleep(1)
            return

        idx = min(self._joystick_index, pygame.joystick.get_count() - 1)
        joy = pygame.joystick.Joystick(idx)
        joy.init()
        logger.info("Joystick başlatıldı [%d]: %s", idx, joy.get_name())

        interval = 1.0 / POLL_HZ
        num_axes    = joy.get_numaxes()
        num_buttons = min(joy.get_numbuttons(), 16)

        def axis(i: int) -> int:
            raw = joy.get_axis(i) if i < num_axes else 0.0
            return int(max(-1000, min(1000, raw * 1000)))

        try:
            while not self.isInterruptionRequested():
                self.heartbeat_counter += 1
                pygame.event.pump()

                x = axis(0)   # Roll / lateral
                y = axis(1)   # Pitch / forward
                z = axis(2)   # Throttle
                r = axis(3)   # Yaw

                buttons = 0
                for b in range(num_buttons):
                    if joy.get_button(b):
                        buttons |= (1 << b)

                self.joystick_updated.emit(x, y, z, r, buttons)
                time.sleep(interval)
        finally:
            pygame.joystick.quit()

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(3000)  # Maks 3s — poll_hz=50, her döngü 20ms
