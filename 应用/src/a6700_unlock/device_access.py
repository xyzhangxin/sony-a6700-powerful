"""Fixed device-access adapter: prepare -> open -> release.

The transaction owns selection, guards and journaling. This adapter only owns
USB access preparation and transport lifecycle; it accepts no address, payload,
backend pathname or arbitrary command from the desktop protocol.
"""
from __future__ import annotations

import sys


class UsbDeviceAccess:
    def __init__(self, profile, backend_factory=None):
        self.profile = profile
        self.backend_factory = backend_factory
        self.backend = None
        self.prepared = False
        self.entered = False

    def _create_backend(self, progress, capture):
        if self.backend_factory is not None:
            try:
                return self.backend_factory(progress, capture=capture)
            except TypeError:
                return self.backend_factory(progress)
        from .usb.backend import UsbBackend
        return UsbBackend(self.profile, progress, capture=capture)

    def prepare_access(self, progress, *, capture=True):
        """Prepare platform access without authenticating the camera transport."""
        if self.prepared:
            raise RuntimeError("设备访问已经准备，不能重复接管。")
        self.backend = self._create_backend(progress, capture)
        prepare = getattr(self.backend, "prepare_access", None)
        if callable(prepare):
            prepare()
        # Legacy/test backends perform their existing preparation in __enter__.
        self.prepared = True
        return dict(stage="access-prepared", platform=sys.platform,
                    capture=bool(capture), authenticated=False)

    def open_transport(self, progress):
        """Open and authenticate the previously prepared transport exactly once."""
        if not self.prepared or self.backend is None or self.entered:
            raise RuntimeError("设备传输尚未准备或已经打开。")
        self.backend.progress = progress
        open_method = getattr(self.backend, "open_transport", None)
        result = open_method() if callable(open_method) else self.backend.__enter__()
        self.entered = True
        # Existing backends return themselves; preserve that contract.
        return self.backend if result is None else result

    def release_access(self):
        """Close only a successfully opened transport; never retry authentication."""
        backend, self.backend = self.backend, None
        entered, self.entered = self.entered, False
        self.prepared = False
        if backend is not None and entered:
            backend.__exit__(None, None, None)
        return dict(cleanup_status=getattr(backend, "cleanup_status", None) if backend is not None else None,
                    cleanup_warning=getattr(backend, "cleanup_warning", None) if backend is not None else None)
