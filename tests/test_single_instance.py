from core.single_instance import (
    ERROR_ALREADY_EXISTS,
    acquire_single_instance_lock,
    is_entry_script_process_running,
    is_single_instance_running,
)


class FakeKernel32:
    def __init__(self, handle=100):
        self.handle = handle
        self.closed = []
        self.names = []

    def CreateMutexW(self, security_attributes, initial_owner, name):
        self.names.append((security_attributes, initial_owner, name))
        return self.handle

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


class FakeProcess:
    def __init__(self, pid, cmdline):
        self.info = {"pid": pid, "cmdline": cmdline}


def test_single_instance_lock_is_noop_outside_windows():
    lock = acquire_single_instance_lock(os_name="posix")

    assert lock.already_running is False
    lock.release()


def test_single_instance_lock_acquires_named_windows_mutex():
    kernel32 = FakeKernel32(handle=123)

    lock = acquire_single_instance_lock(
        os_name="nt",
        kernel32=kernel32,
        get_last_error=lambda: 0,
    )

    assert lock.already_running is False
    assert kernel32.names == [(None, True, "VCPHunterQuantTerminal_SingleInstance")]

    lock.release()

    assert kernel32.closed == [123]


def test_single_instance_lock_closes_duplicate_mutex_handle():
    kernel32 = FakeKernel32(handle=456)

    lock = acquire_single_instance_lock(
        os_name="nt",
        kernel32=kernel32,
        get_last_error=lambda: ERROR_ALREADY_EXISTS,
    )

    assert lock.already_running is True
    assert kernel32.closed == [456]

    lock.release()

    assert kernel32.closed == [456]


def test_single_instance_probe_detects_existing_mutex():
    kernel32 = FakeKernel32(handle=789)

    running = is_single_instance_running(
        os_name="nt",
        kernel32=kernel32,
        get_last_error=lambda: ERROR_ALREADY_EXISTS,
    )

    assert running is True
    assert kernel32.names == [(None, False, "VCPHunterQuantTerminal_SingleInstance")]
    assert kernel32.closed == [789]


def test_single_instance_probe_closes_new_mutex():
    kernel32 = FakeKernel32(handle=987)

    running = is_single_instance_running(
        os_name="nt",
        kernel32=kernel32,
        get_last_error=lambda: 0,
    )

    assert running is False
    assert kernel32.names == [(None, False, "VCPHunterQuantTerminal_SingleInstance")]
    assert kernel32.closed == [987]


def test_entry_script_process_probe_detects_other_process():
    running = is_entry_script_process_running(
        "D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw",
        current_pid=10,
        process_iter=lambda _attrs: [
            FakeProcess(10, ["pythonw.exe", "D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw"]),
            FakeProcess(11, ["C:/Python314/pythonw.exe", '"D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw"']),
        ],
        process_has_visible_window=lambda pid: pid == 11,
    )

    assert running is True


def test_entry_script_process_probe_ignores_current_process():
    running = is_entry_script_process_running(
        "D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw",
        current_pid=10,
        process_iter=lambda _attrs: [
            FakeProcess(10, ["pythonw.exe", "D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw"]),
        ],
        process_has_visible_window=lambda _pid: True,
    )

    assert running is False


def test_entry_script_process_probe_ignores_windowless_stale_process():
    running = is_entry_script_process_running(
        "D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw",
        current_pid=10,
        process_iter=lambda _attrs: [
            FakeProcess(11, ["C:/Python314/pythonw.exe", "D:/vcp_hunter/紫金研选/vcp_hunter_qt.pyw"]),
        ],
        process_has_visible_window=lambda _pid: False,
    )

    assert running is False
