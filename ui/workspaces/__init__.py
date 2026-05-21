# -*- coding: utf-8 -*-
from __future__ import annotations


def __getattr__(name: str):
    if name == "ClassicWorkspace":
        from ui.workspaces.classic_workspace import ClassicWorkspace

        return ClassicWorkspace
    raise AttributeError(name)


__all__ = ["ClassicWorkspace"]
