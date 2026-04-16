# -*- coding: utf-8 -*-
from ui.components.toast_widget import Toast


def test_warning_toast_hides_icon():
    toast = Toast(duration=10)
    try:
        toast.show_toast("代码不在当前 A 股股票列表中", "warning")

        assert toast.lbl_icon.text() == ""
        assert toast.lbl_icon.isHidden() is True
    finally:
        toast.hide_toast()
        toast.close()


def test_success_toast_keeps_icon_visible():
    toast = Toast(duration=10)
    try:
        toast.show_toast("已加入关注池", "success")

        assert toast.lbl_icon.text() == "OK"
        assert toast.lbl_icon.isHidden() is False
    finally:
        toast.hide_toast()
        toast.close()
