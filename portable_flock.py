"""POSIX `fcntl.flock` için Windows uyumluluk katmanı.

Linux/Replit üzerinde bu modül HİÇ KULLANILMAZ: paylaşımlı durum
modülleri önce gerçek `fcntl`'i dener ve yalnız o yoksa (Windows)
buraya düşer. Böylece POSIX davranışı sıfır değişiklikle korunur.

Windows'ta `msvcrt.locking` ile dosyanın 0. baytı kilitlenir; tüm
çağıranlar aynı yardımcıyı kullandığı için bu, tek makinede süreçler
arası dışlama (mutual exclusion) sağlamaya yeterlidir.

API: `flock(fh, LOCK_EX | LOCK_NB)` / `flock(fh, LOCK_UN)` — gerçek
fcntl ile aynı imza ve BlockingIOError/OSError semantiği.
"""
from __future__ import annotations

import os

if os.name == "nt":  # pragma: no cover — yalnız Windows'ta çalışır
    import msvcrt

    LOCK_EX = 0x02
    LOCK_NB = 0x04
    LOCK_UN = 0x08

    def _fd(fh) -> int:
        return fh if isinstance(fh, int) else fh.fileno()

    def flock(fh, flags: int) -> None:
        fd = _fd(fh)
        os.lseek(fd, 0, os.SEEK_SET)
        if flags & LOCK_UN:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # zaten kilitli değil — fcntl LOCK_UN gibi sessiz
            return
        mode = msvcrt.LK_NBLCK if flags & LOCK_NB else msvcrt.LK_LOCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as exc:
            if flags & LOCK_NB:
                raise BlockingIOError(
                    exc.errno or 11, "lock held") from exc
            raise
else:
    # POSIX: gerçek fcntl'e birebir vekâlet (davranış değişmez).
    import fcntl as _fcntl

    LOCK_EX = _fcntl.LOCK_EX
    LOCK_NB = _fcntl.LOCK_NB
    LOCK_UN = _fcntl.LOCK_UN

    def flock(fh, flags: int) -> None:
        _fcntl.flock(fh, flags)
