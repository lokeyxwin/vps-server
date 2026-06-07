"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-SCHEMA-VPS-v4 VPSRecord schema v4 重塑

故事:
  task/01 把旧 XrayStatus 5 值状态机改成 VPSStage 2 值占用状态机,
  字段改名 (xray_status → stage / idle_port_count → used_port_count),
  删冗余字段 (xray_status_message: 错误信息只住 vps_task),
  port 字段去掉 default=22 (业务层必填强制)。

  本测试在内存 SQLite 上验证 schema 改完后的 8 条行为契约,
  不污染 dev DB,不依赖任何 worker / service 实现。

测试矩阵 (8 TC):
  TC-01 新建 VPSRecord, stage 默认值 "connectable"
  TC-02 新建 VPSRecord, used_port_count 默认值 0
  TC-03 旧字段 (xray_status / xray_status_message / stage_message /
        idle_port_count) 不存在于 ORM mapping
  TC-04 VPSStage 类只有 2 个常量 (CONNECTABLE / RUNNING),
        旧值 (UNREACHABLE / NOT_INSTALLED / INSTALLING / STOPPED 等) 不存在
  TC-05 stage 字段能存 CONNECTABLE / RUNNING 两值, round-trip 正确
  TC-06 port 字段无 SQLAlchemy default (业务层必填强制)
  TC-07 used_port_count 可被业务层 update (0 → 5 → 3)
  TC-08 __repr__ 含 stage, 不含 password / username

不应发生:
  - 任何写库动作落到 db/vps_server.db (用独立 in-memory engine)
  - import services/*
========================================================================
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import VPSRecord, VPSStage


def _make_in_memory_engine():
    """每个 TestCase 自带一个全新的 in-memory SQLite engine + sessionmaker。

    不复用 db.session.SessionLocal —— 那是绑在 dev DB 上的,会污染。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[VPSRecord.__table__])
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


class TestVPSRecordSchemaV4(unittest.TestCase):
    """schema v4 行为契约 8 条。"""

    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

    def tearDown(self):
        self.engine.dispose()

    # ---------- TC-01 ----------
    def test_tc01_stage_default_is_connectable(self):
        """新建 VPSRecord, stage 默认 connectable。"""
        rec = VPSRecord.from_form(
            ip="10.0.0.1", username="root", password="x", port=22
        )
        with self.Session() as s:
            s.add(rec)
            s.commit()
            s.refresh(rec)
            self.assertEqual(rec.stage, VPSStage.CONNECTABLE)
            self.assertEqual(rec.stage, "connectable")

    # ---------- TC-02 ----------
    def test_tc02_used_port_count_default_is_zero(self):
        """新建 VPSRecord, used_port_count 默认 0。"""
        rec = VPSRecord.from_form(
            ip="10.0.0.2", username="root", password="x", port=22
        )
        with self.Session() as s:
            s.add(rec)
            s.commit()
            s.refresh(rec)
            self.assertEqual(rec.used_port_count, 0)

    # ---------- TC-03 ----------
    def test_tc03_old_fields_no_longer_in_mapping(self):
        """xray_status / xray_status_message / stage_message / idle_port_count
        在 v4 schema 中应该完全删除 (ORM 映射不存在)。"""
        mapper_columns = {c.key for c in inspect(VPSRecord).mapper.columns}
        for ghost in (
            "xray_status",
            "xray_status_message",
            "stage_message",
            "idle_port_count",
        ):
            self.assertNotIn(
                ghost,
                mapper_columns,
                f"旧字段 {ghost} 应该已从 VPSRecord ORM mapping 中删除",
            )

    # ---------- TC-04 ----------
    def test_tc04_vps_stage_has_only_two_constants(self):
        """VPSStage 只有 2 个值,旧 XrayStatus 的所有值都不应再存在。"""
        self.assertEqual(VPSStage.CONNECTABLE, "connectable")
        self.assertEqual(VPSStage.RUNNING, "running")

        # 旧状态值在 v4 全部不应存在
        for ghost in (
            "UNREACHABLE",   # v3 曾计划保留,v4 删除
            "NOT_INSTALLED", # 旧 XrayStatus
            "INSTALLING",
            "INSTALL_FAILED",
            "STOPPED",
            "UNINSTALLED",
        ):
            self.assertFalse(
                hasattr(VPSStage, ghost),
                f"VPSStage 不应再有 {ghost}",
            )

    # ---------- TC-05 ----------
    def test_tc05_stage_field_round_trips_both_values(self):
        """CONNECTABLE 和 RUNNING 都能写入 + 查回。"""
        rec_a = VPSRecord.from_form(
            ip="10.0.0.10", username="root", password="x", port=22
        )
        rec_b = VPSRecord.from_form(
            ip="10.0.0.11", username="root", password="x", port=22
        )
        rec_b.stage = VPSStage.RUNNING

        with self.Session() as s:
            s.add_all([rec_a, rec_b])
            s.commit()

        with self.Session() as s:
            got_a = s.query(VPSRecord).filter_by(ip="10.0.0.10").one()
            got_b = s.query(VPSRecord).filter_by(ip="10.0.0.11").one()
            self.assertEqual(got_a.stage, "connectable")
            self.assertEqual(got_b.stage, "running")

    # ---------- TC-06 ----------
    def test_tc06_port_field_has_no_default(self):
        """port 字段在 ORM column 层面无 default (业务层必填强制)。"""
        port_col = inspect(VPSRecord).mapper.columns["port"]
        self.assertIsNone(
            port_col.default,
            "port 字段不应有 SQLAlchemy default (旧的 default=22 已删除)",
        )
        self.assertIsNone(
            port_col.server_default,
            "port 字段也不应有 server_default",
        )
        # 业务函数 from_form 的签名也应反映 port 必填
        import inspect as py_inspect
        sig = py_inspect.signature(VPSRecord.from_form)
        port_param = sig.parameters["port"]
        self.assertIs(
            port_param.default,
            py_inspect.Parameter.empty,
            "from_form(port=...) 不应有默认值",
        )

    # ---------- TC-07 ----------
    def test_tc07_used_port_count_can_be_updated(self):
        """used_port_count 0 → 5 → 3 round-trip 正确。"""
        rec = VPSRecord.from_form(
            ip="10.0.0.20", username="root", password="x", port=22
        )
        with self.Session() as s:
            s.add(rec)
            s.commit()

        with self.Session() as s:
            r = s.query(VPSRecord).filter_by(ip="10.0.0.20").one()
            r.used_port_count = 5
            s.commit()

        with self.Session() as s:
            self.assertEqual(
                s.query(VPSRecord).filter_by(ip="10.0.0.20").one().used_port_count,
                5,
            )

        with self.Session() as s:
            r = s.query(VPSRecord).filter_by(ip="10.0.0.20").one()
            r.used_port_count = 3
            s.commit()

        with self.Session() as s:
            self.assertEqual(
                s.query(VPSRecord).filter_by(ip="10.0.0.20").one().used_port_count,
                3,
            )

    # ---------- TC-08 ----------
    def test_tc08_repr_exposes_stage_without_secrets(self):
        """__repr__ 含 stage,不含 password / username。"""
        rec = VPSRecord.from_form(
            ip="10.0.0.99",
            username="ultra_secret_user",
            password="SuperSecretPwd",
            port=22,
        )
        repr_str = repr(rec)
        # 必须含 stage 标签
        self.assertIn("stage=", repr_str)
        # 不应泄漏 username / password 明文
        self.assertNotIn("ultra_secret_user", repr_str)
        self.assertNotIn("SuperSecretPwd", repr_str)
        # 不应出现 password 字段名(避免误暴露密文字段名)
        self.assertNotIn("password", repr_str.lower())


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-06（独立 in-memory SQLite，0.037s，8/8 全过）
# 偏差：
#   - 测试用独立 in-memory SQLite engine + 自建 sessionmaker，
#     不复用 db.session.SessionLocal（避免污染 dev DB）。
# 待用户决策事项：无
# ========================================================================
