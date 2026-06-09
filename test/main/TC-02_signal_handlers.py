"""TC-18-02 _install_signal_handlers 设置成功.

调 _install_signal_handlers 后, SIGTERM / SIGINT 的 handler 都不再是默认值.
"""

from __future__ import annotations

import signal

import main


def test_install_signal_handlers_overrides_defaults():
    """调 _install_signal_handlers 后, SIGTERM / SIGINT handler 不是 SIG_DFL."""
    # 先存原 handler, 测完恢复
    orig_term = signal.getsignal(signal.SIGTERM)
    orig_int = signal.getsignal(signal.SIGINT)
    try:
        main._install_signal_handlers()
        assert signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
        assert signal.getsignal(signal.SIGINT) != signal.SIG_DFL
        # 应该是同一个 callable (本模块内部 _handler)
        assert callable(signal.getsignal(signal.SIGTERM))
        assert callable(signal.getsignal(signal.SIGINT))
    finally:
        signal.signal(signal.SIGTERM, orig_term)
        signal.signal(signal.SIGINT, orig_int)


def test_handler_sets_stop_flag(monkeypatch):
    """触发 handler → _stop 应被置 True."""
    monkeypatch.setattr(main, "_stop", False)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        main._install_signal_handlers()
        # 拿到刚装的 handler 直接调用
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        assert main._stop is True
    finally:
        signal.signal(signal.SIGTERM, orig_term)
        # 清理: 测试影响的 _stop 在下一个用例不可见, 因为下一个 test 也会 monkeypatch
