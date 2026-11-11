"""Guided FL23 reproduction watcher; read-only USB identity observation."""
import time


def observe_msc(observer=None):
    if observer is None:
        from .usb.legacy import observed_sony_product_ids
        observer = observed_sony_product_ids
    return observer() == [0x0e76]


def stable_msc_observed(timeout_seconds=120, samples=4, interval=0.5, observer=None):
    """Require several consecutive observations before authentication."""
    deadline = time.monotonic() + timeout_seconds
    streak = 0
    while time.monotonic() < deadline:
        if observe_msc(observer):
            streak += 1
            if streak >= samples:
                return True
        else:
            streak = 0
        time.sleep(interval)
    return False


def watch(cancel, on_event, observer=None, interval=1.0, timeout=1800, stable_observations=4):
    """Wait for deliberate unplug, camera operation, and reconnect for retrieval.

    No authentication, payload upload, reset, retry, or device lock is used.
    The caller may stop waiting at any time and collect explicitly instead.
    """
    started = time.monotonic()
    disconnected = False
    unplug_notified = False
    stable = 0
    while not cancel.is_set() and time.monotonic() - started < timeout:
        present = observe_msc(observer)
        elapsed = int((time.monotonic() - started) * 1000)
        if not disconnected:
            if present:
                if not unplug_notified:
                    unplug_notified = True
                    on_event('unplug_required', elapsed)
            else:
                disconnected = True
                on_event('user_action_required', elapsed)
        elif disconnected:
            if not present:
                stable = 0
            else:
                stable += 1
                if stable >= stable_observations:
                    on_event('reconnect_observed', elapsed)
                    return 'reconnected'
        cancel.wait(interval)
    return 'cancelled' if cancel.is_set() else 'timeout'
