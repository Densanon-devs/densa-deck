"""Optional camera + OCR backends for the scanner.

Everything here is optional and lazily imported. The identification logic in
`scanner.py` is pure text -> candidates, so the app works without any of it —
you can type a name, paste OCR output from any tool, or read a card's footer
off your phone.

Why optional rather than bundled: `opencv-python-headless` is roughly 50 MB
against a 107 MB installer, for a feature most users never touch. This
project has already shipped four releases broken by PyInstaller bundling
problems; adding half an installer of native code for an optional feature is
the wrong trade. The analyst GGUF and the rulings file set the precedent —
download it if you want it.

OCR preference order, best-first:

  1. **Windows.Media.Ocr** via winrt — already on every Windows 10+ machine,
     zero download, no native wheel to bundle. This is the default target.
  2. **Tesseract** via pytesseract, if the user already has it.
  3. Nothing — the UI falls back to manual entry, which still works.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BackendStatus:
    name: str
    available: bool
    detail: str = ""
    install_hint: str = ""


class WindowsOcrBackend:
    """Windows.Media.Ocr through the winrt bridge.

    Preferred because it costs nothing to ship: the engine is part of the OS
    on every Windows 10+ install.
    """

    name = "windows-ocr"

    def available(self) -> bool:
        try:
            import winrt.windows.media.ocr  # noqa: F401
            return True
        except Exception:
            return False

    def status(self) -> BackendStatus:
        ok = self.available()
        return BackendStatus(
            name=self.name,
            available=ok,
            detail="Built into Windows 10+" if ok else "winrt bridge not installed",
            install_hint="" if ok else "pip install winrt-Windows.Media.Ocr",
        )

    def read_text(self, image_path) -> str:
        """OCR an image file. Returns '' rather than raising on failure.

        A scanner that crashes mid-box is worse than one that reports an
        unreadable card and moves on.
        """
        try:
            import asyncio

            from winrt.windows.graphics.imaging import BitmapDecoder
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.storage import FileAccessMode, StorageFile

            async def _run():
                file = await StorageFile.get_file_from_path_async(str(image_path))
                stream = await file.open_async(FileAccessMode.READ)
                decoder = await BitmapDecoder.create_async(stream)
                bitmap = await decoder.get_software_bitmap_async()
                engine = OcrEngine.try_create_from_user_profile_languages()
                if engine is None:
                    return ""
                result = await engine.recognize_async(bitmap)
                return result.text or ""

            return asyncio.run(_run())
        except Exception:
            return ""


class TesseractBackend:
    """pytesseract, when the user already has Tesseract installed."""

    name = "tesseract"

    def available(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def status(self) -> BackendStatus:
        ok = self.available()
        return BackendStatus(
            name=self.name,
            available=ok,
            detail="Tesseract found" if ok else "Tesseract not installed",
            install_hint="" if ok else "Install Tesseract, then: pip install pytesseract",
        )

    def read_text(self, image_path) -> str:
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(image_path)) or ""
        except Exception:
            return ""


class ManualBackend:
    """Always available. The user types or pastes the text themselves.

    Not a placeholder — reading the four characters in a card's corner is
    genuinely faster than fighting a bad webcam, and it means the scanner
    feature is never entirely unavailable.
    """

    name = "manual"

    def available(self) -> bool:
        return True

    def status(self) -> BackendStatus:
        return BackendStatus(
            name=self.name, available=True,
            detail="Type a card name, or its set code and collector number",
        )

    def read_text(self, image_path) -> str:
        return ""


def camera_status() -> BackendStatus:
    """Whether frame capture is possible on this machine."""
    try:
        import cv2  # noqa: F401
        return BackendStatus(
            name="opencv", available=True, detail="OpenCV available")
    except Exception:
        return BackendStatus(
            name="opencv", available=False,
            detail="Camera capture needs OpenCV (optional, ~50 MB)",
            install_hint="pip install opencv-python-headless",
        )


def best_ocr_backend():
    """The best OCR engine available, falling back to manual entry."""
    for backend in (WindowsOcrBackend(), TesseractBackend()):
        if backend.available():
            return backend
    return ManualBackend()


def scan_capabilities() -> dict:
    """What this machine can actually do, for the UI to render honestly."""
    ocr = [WindowsOcrBackend().status(), TesseractBackend().status()]
    cam = camera_status()
    active = best_ocr_backend()
    return {
        "ocr_backends": [
            {"name": s.name, "available": s.available, "detail": s.detail,
             "install_hint": s.install_hint}
            for s in ocr
        ],
        "camera": {"name": cam.name, "available": cam.available,
                   "detail": cam.detail, "install_hint": cam.install_hint},
        "active_ocr": active.name,
        # Manual entry always works, so the feature is never fully blocked.
        "manual_always_available": True,
    }
