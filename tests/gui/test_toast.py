"""Interaction regressions for notifications over native console widgets."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMainWindow, QTextEdit, QToolButton, QWidget
from shiboken6 import isValid

from optiland_gui.widgets import toast as toast_module


@pytest.fixture
def toast_manager(qapp):
    window = QMainWindow()
    window.setGeometry(50, 50, 900, 700)
    console = QTextEdit()
    # VTK creates native widgets in the real GUI, including sibling docks.
    console.setAttribute(Qt.WA_NativeWindow)
    window.setCentralWidget(console)
    window.show()
    manager = toast_module.ToastManager(window)
    yield manager
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def show_error(manager):
    manager.notify(
        "An error occurred during OPD:\nPolarization must be set when surfaces "
        "have polarization-dependent coatings.",
        "error",
        sub_message="Select polarization and retry.",
    )
    toast = manager._stack[-1]
    QTest.qWait(toast_module._ENTER_DURATION + 100)
    return toast


def click_target(toast, target):
    if target == "close":
        return toast.findChild(QToolButton, "ToastCloseButton")
    if target == "background":
        return toast.findChild(QWidget, "ToastOuter")
    return toast.findChildren(QLabel)[{"icon": 0, "message": 1, "detail": 2}[target]]


@pytest.mark.parametrize("target", ["icon", "message", "detail", "background", "close"])
def test_click_dismisses_notification(toast_manager, target):
    toast = show_error(toast_manager)
    widget = click_target(toast, target)
    QTest.mouseClick(widget, Qt.LeftButton)
    QTest.qWait(toast_module._EXIT_DURATION + 100)
    assert toast_manager._stack == []
    assert not isValid(toast)


def test_close_button_keyboard_activation(toast_manager):
    toast = show_error(toast_manager)
    button = click_target(toast, "close")
    assert button.accessibleName() == "Dismiss notification"
    button.setFocus()
    QTest.keyClick(button, Qt.Key_Space)
    QTest.qWait(toast_module._EXIT_DURATION + 100)
    assert toast_manager._stack == []
    assert not isValid(toast)


@pytest.mark.parametrize("severity", ["info", "success", "warning", "error"])
def test_timeout_preserves_errors_only(toast_manager, monkeypatch, severity):
    monkeypatch.setattr(toast_module, "_AUTO_DISMISS_MS", 300)
    toast_manager.notify("Notification", severity)
    toast = toast_manager._stack[-1]
    QTest.qWait(300 + toast_module._EXIT_DURATION + 150)
    if severity == "error":
        assert toast_manager._stack == [toast]
        assert toast.isVisible()
    else:
        assert toast_manager._stack == []
        assert not isValid(toast)


def test_dismissal_restacks_remaining_notifications(toast_manager):
    first = show_error(toast_manager)
    second = show_error(toast_manager)
    QTest.mouseClick(click_target(second, "close"), Qt.LeftButton)
    # Repeated input while exiting must not start a second dismissal.
    QTest.mouseClick(click_target(second, "close"), Qt.LeftButton)
    QTest.qWait(toast_module._EXIT_DURATION + toast_module._SHIFT_DURATION + 100)
    assert toast_manager._stack == [first]
    assert first.pos() == toast_manager._target_pos(0, first)
    assert not isValid(second)


@pytest.mark.parametrize("target", ["icon", "message", "close"])
def test_windows_hit_testing_keeps_clicks_out_of_console(toast_manager, target, qapp):
    if qapp.platformName() != "windows":
        pytest.skip("Requires native Windows hit testing (not the offscreen plugin)")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND

    window = toast_manager._parent
    # WindowFromPoint uses desktop stacking, so keep this short-lived test
    # window above unrelated applications while sampling the native target.
    window.setWindowFlag(Qt.WindowStaysOnTopHint)
    window.show()
    toast = show_error(toast_manager)
    window.raise_()
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window)
    widget = click_target(toast, target)
    global_point = widget.mapToGlobal(widget.rect().center())
    local_point = window.mapFromGlobal(global_point)
    ratio = window.devicePixelRatioF()
    native_point = wintypes.POINT(
        round(local_point.x() * ratio), round(local_point.y() * ratio)
    )
    assert user32.ClientToScreen(window.winId(), ctypes.byref(native_point))
    recipient = QWidget.find(user32.WindowFromPoint(native_point))

    # Sending directly to a QLabel (or using QApplication.widgetAt) bypasses
    # Windows' layered-window hit testing and misses the original regression.
    assert recipient is not None
    assert recipient is toast or toast.isAncestorOf(recipient)
    QTest.mouseClick(
        recipient.windowHandle(),
        Qt.LeftButton,
        pos=recipient.mapFromGlobal(global_point),
    )
    QTest.qWait(toast_module._EXIT_DURATION + 100)
    assert toast_manager._stack == []
    assert not isValid(toast)
