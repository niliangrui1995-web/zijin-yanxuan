# -*- coding: utf-8 -*-
"""Shared task-layer exceptions."""


class UserFacingTaskError(Exception):
    """预期内、可恢复的后台任务失败。"""

    def __init__(self, user_message: str, log_message: str | None = None):
        super().__init__(user_message)
        self.user_message = str(user_message or "").strip()
        self.log_message = str(log_message or self.user_message).strip()
