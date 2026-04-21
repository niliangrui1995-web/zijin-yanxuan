from core.process_watchdog import dump_main_thread_stack, memory_bucket_index


def test_memory_bucket_index_returns_negative_one_below_threshold():
    assert memory_bucket_index(1399, threshold_mb=1400, step_mb=256) == -1


def test_memory_bucket_index_grows_in_steps():
    assert memory_bucket_index(1400, threshold_mb=1400, step_mb=256) == 0
    assert memory_bucket_index(1657, threshold_mb=1400, step_mb=256) == 1


def test_dump_main_thread_stack_contains_current_test_name():
    stack_text = dump_main_thread_stack()

    assert "test_dump_main_thread_stack_contains_current_test_name" in stack_text
