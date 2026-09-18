"""Real in-process consoles own one document namespace and callback lifetime."""

from __future__ import annotations

import copy
import sys
from types import SimpleNamespace

import pytest
from IPython import get_ipython
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMainWindow

from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.widgets.python_terminal import (
    PythonTerminalWidget,
    _active_document_shell,
)


def drain(qapp):
    for _ in range(5):
        qapp.processEvents()


@pytest.fixture
def console_windows(qapp, minimal_optic):
    windows, connectors = [], []

    class Window(QMainWindow):
        def closeEvent(self, event):
            self.terminal.shutdown_kernel()
            super().closeEvent(event)

    def create():
        connector = OptilandConnector()
        connectors.append(connector)
        connector.load_optic_from_object(copy.deepcopy(minimal_optic))
        window = Window()
        windows.append(window)
        window.terminal = PythonTerminalWidget(
            window,
            custom_variables={"connector": connector, "iface": SimpleNamespace()},
        )
        window.setCentralWidget(window.terminal)
        window.terminal.commandExecuted.connect(connector.opticChanged.emit)
        window.show()
        drain(qapp)
        return window, connector

    yield create
    for window in reversed(windows):
        window.close()
        window.deleteLater()
    for connector in connectors:
        connector._backend_timer.stop()
        connector.calculation_jobs.shutdown()
        connector.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def state(connector):
    return (
        connector.get_optic(),
        connector.document_state.token,
        connector.document_state.edit_token,
        connector._undo_redo_manager.revision,
        connector.get_optic().surfaces[1].thickness,
    )


def execute(window, source, qapp):
    window.terminal.kernel_client.execute(source, silent=False)
    drain(qapp)
    shell = window.terminal.kernel_manager.kernel.shell
    assert shell.last_execution_succeeded
    return shell


def test_two_windows_keep_namespaces_events_and_document_tokens_separate(
    console_windows, qapp
):
    application_main = sys.modules["__main__"]
    previous_shell = get_ipython()
    first, one = console_windows()
    first_shell = execute(
        first,
        "window_value = 'first'; connector.get_optic().surfaces[1].thickness = 6",
        qapp,
    )
    original = state(one)
    second, two = console_windows()
    assert state(one) == original
    second_shell = second.terminal.kernel_manager.kernel.shell
    assert second_shell is not first_shell
    assert second_shell.user_ns is not first_shell.user_ns
    assert second_shell.events is not first_shell.events
    assert first_shell.kernel is first.terminal.kernel_manager.kernel
    second_initial = state(two)
    execute(
        second,
        "assert 'window_value' not in globals()\n"
        "from IPython import get_ipython as imported_ipython\n"
        "assert imported_ipython() is get_ipython()\n"
        "window_value = 'second'\n"
        "connector.get_optic().surfaces[1].thickness = 9",
        qapp,
    )
    assert state(one) == original
    assert two.document_state.token.revision == second_initial[1].revision + 1
    assert two.get_optic().surfaces[1].thickness == 9
    second_after = state(two)
    execute(
        first,
        "assert window_value == 'first'\n"
        "from IPython import get_ipython as imported_ipython\n"
        "assert imported_ipython() is get_ipython()\n"
        "connector.get_optic().surfaces[1].thickness = 7",
        qapp,
    )
    assert state(two) == second_after
    assert one.document_state.token.revision == original[1].revision + 1
    assert one.get_optic().surfaces[1].thickness == 7
    assert get_ipython() is previous_shell
    assert sys.modules["__main__"] is application_main
    original = state(one)
    second.close()
    drain(qapp)
    assert state(one) == original
    assert (
        second.terminal._on_kernel_execute
        not in (second_shell.events.callbacks["post_execute"])
    )
    assert "connector" not in second_shell.user_ns
    execute(first, "assert window_value == 'first'; after_close = True", qapp)
    assert first_shell.user_ns["after_close"] is True
    second.terminal.shutdown_kernel()
    second.terminal.kernel_manager.shutdown_kernel()


def test_nested_shell_activation_and_failed_commands_restore_global_owner(
    console_windows, qapp
):
    first, one = console_windows()
    second, two = console_windows()
    a = first.terminal.kernel_manager.kernel.shell
    b = second.terminal.kernel_manager.kernel.shell
    previous = get_ipython()
    application_main = sys.modules["__main__"]
    with _active_document_shell(a):
        assert get_ipython() is a
        with (
            pytest.raises(RuntimeError, match="restore"),
            _active_document_shell(b),
        ):
            assert get_ipython() is b
            raise RuntimeError("restore")
        assert get_ipython() is a
    assert get_ipython() is previous
    assert sys.modules["__main__"] is application_main
    before_one, before_two = state(one), state(two)
    first.terminal.kernel_client.execute("raise ValueError('user failure')")
    drain(qapp)
    assert not a.last_execution_succeeded
    assert one.document_state.token.revision == before_one[1].revision + 1
    assert state(two) == before_two
    assert get_ipython() is previous
    execute(second, "survived = True", qapp)
    assert b.user_ns["survived"] is True


def test_silent_requests_and_nested_commands_keep_their_shell_owner(
    console_windows, qapp
):
    first, one = console_windows()
    second, two = console_windows()
    a = first.terminal.kernel_manager.kernel.shell
    b = second.terminal.kernel_manager.kernel.shell
    before_one, before_two = state(one), state(two)
    first.terminal.kernel_client.execute("silent_value = 42", silent=True)
    drain(qapp)
    assert a.user_ns["silent_value"] == 42
    assert "silent_value" not in b.user_ns
    assert one.document_state.token.revision == before_one[1].revision + 1
    assert state(two) == before_two

    def nested():
        second.terminal.kernel_client.execute("nested_value = 'second'", silent=False)

    a.push({"nested": nested})
    execute(
        first,
        "from IPython import get_ipython as imported_ipython\n"
        "nested()\n"
        "assert imported_ipython() is get_ipython()\n"
        "nested_value = 'first'",
        qapp,
    )
    assert a.user_ns["nested_value"] == "first"
    assert b.user_ns["nested_value"] == "second"
    assert two.document_state.token.revision == before_two[1].revision + 1
