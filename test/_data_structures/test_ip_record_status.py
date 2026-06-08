"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-SCHEMA-IPRECORD-STATUS IPRecord.status + IPStatus 枚举 (T-11)

故事:
  T-11 给 ip_record 加业务流转状态机字段 status (跟 is_active 独立维度)。
  IPProbeWorker 入库时永远写 usable; ProxyDeployWorker 配置成功改 using。

  spec test/ip_probe_worker/spec.md v2 §6 / §G 锚点:
    - IPStatus.USABLE = "usable"
    - IPStatus.USING  = "using"
    - IPRecord.from_form 默认带 status=USABLE
    - 字段 String(16) default=USABLE nullable=False

测试矩阵 (6 TC):
  TC-01 IPStatus 常量值正确 ("usable" / "using")
  TC-02 IPRecord.from_form 不传 status -> 默认 USABLE
  TC-03 显式建 IPRecord(...) 不传 status -> 默认 USABLE (server/ORM default)
  TC-04 显式建 IPRecord(..., status="using") -> 落库后仍是 using
  TC-05 update status 为 using -> 重新查仍是 using
  TC-06 status 字段类型 / nullable 校验 (schema 自描述)

不应发生:
  - 任何写库动作落到 db/vps_server.db (用独立 in-memory engine)
========================================================================
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IPRecord, IPStatus


def _make_in_memory_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[IPRecord.__table__])
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _new_ip(
    session,
    egress_ip: str = "1.1.1.1",
    status: str | None = None,
) -> IPRecord:
    """测试夹具: 用 ORM 显式建一条 IPRecord (绕过 from_form 测裸字段默认值)。"""
    kwargs = dict(
        entry_host="proxy.example.com",
        entry_port=1080,
        username="u",
        password_encrypted=b"x",
        protocol="socks5",
        egress_ip=egress_ip,
    )
    if status is not None:
        kwargs["status"] = status
    rec = IPRecord(**kwargs)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return rec


class TestIPRecordStatus(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

    def tearDown(self):
        self.engine.dispose()

    # ---------- TC-01 ----------
    def test_tc01_ipstatus_constants(self):
        self.assertEqual(IPStatus.USABLE, "usable")
        self.assertEqual(IPStatus.USING, "using")

    # ---------- TC-02 ----------
    def test_tc02_from_form_default_status_usable(self):
        """IPRecord.from_form(...) 不传 status, 实例 status 为 usable。"""
        rec = IPRecord.from_form(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="u",
            password="p",
            protocol="socks5",
            egress_ip="2.2.2.2",
        )
        self.assertEqual(rec.status, IPStatus.USABLE)
        self.assertEqual(rec.status, "usable")

    # ---------- TC-03 ----------
    def test_tc03_default_status_on_insert(self):
        """裸 IPRecord(...) 不传 status, 落库后 ORM default 写 usable。"""
        with self.Session() as s:
            rec = _new_ip(s, egress_ip="3.3.3.3")
            self.assertEqual(rec.status, IPStatus.USABLE)

    # ---------- TC-04 ----------
    def test_tc04_explicit_using_persists(self):
        """显式传 status='using' 落库后仍是 using。"""
        with self.Session() as s:
            rec = _new_ip(s, egress_ip="4.4.4.4", status=IPStatus.USING)
            self.assertEqual(rec.status, "using")

    # ---------- TC-05 ----------
    def test_tc05_update_status_to_using_persists(self):
        with self.Session() as s:
            rec = _new_ip(s, egress_ip="5.5.5.5")
            rec_id = rec.id

        with self.Session() as s:
            r = s.get(IPRecord, rec_id)
            self.assertEqual(r.status, IPStatus.USABLE)
            r.status = IPStatus.USING
            s.commit()

        with self.Session() as s:
            r = s.get(IPRecord, rec_id)
            self.assertEqual(r.status, "using")

    # ---------- TC-06 ----------
    def test_tc06_status_column_schema(self):
        """schema 自描述: status 是 VARCHAR(16), NOT NULL。"""
        cols = {c["name"]: c for c in inspect(self.engine).get_columns("ip_record")}
        self.assertIn("status", cols, "ip_record 表应有 status 列")
        col = cols["status"]
        self.assertFalse(col["nullable"], "status NOT NULL")
        # SQLite 反射: 类型字符串如 "VARCHAR(16)"
        self.assertIn("VARCHAR", str(col["type"]).upper())


if __name__ == "__main__":
    unittest.main()
