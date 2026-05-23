from core.single_instance import ERROR_ALREADY_EXISTS, acquire_single_instance_lock


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
