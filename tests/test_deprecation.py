# -*- coding: utf-8 -*-
"""ENG-008 弃用与迁移机制：装饰器真的会提醒、迁移表真的完整、版本元组真的可比。

为什么单独有这一组测试
----------------------
「加了 DeprecationWarning 机制」这句话光写代码不成立 —— 只有**有人抓 warning** 才算数。
同理，`MIGRATIONS` 是 CHANGELOG / README 生成块的单一事实源，字段缺一个就会渲染出
「变了什么＝空」这种没法读的表，所以这里按**字段完整性**断言，而不是只 import 一下。

红线（也在这里钉住）：弃用期内行为**必须与旧版逐位一致** —— 只是提醒，不是偷偷改语义。
"""
from __future__ import annotations

import warnings

import pytest

import tdxdata
from tdxdata import _deprecation as dep


@pytest.fixture(autouse=True)
def _clean_seen():
    """每条用例前清掉"已提醒"记录，否则第一条用例之后全都静默。"""
    dep.reset_seen()
    yield
    dep.reset_seen()


# ══════════════════════════════════════════════════════════════════════════
# 一、机制：装饰器 / warn
# ══════════════════════════════════════════════════════════════════════════
def test_deprecated_decorator_warns_and_still_works():
    """提醒必须带「替代品」和「移除版本」，且被装饰的函数**照常返回原值**。"""
    @dep.deprecated("new_thing()", since="0.4.0", remove_in="0.6.0")
    def old_thing(x):
        """旧接口。"""
        return x * 2

    with warnings.catch_warnings(record=True) as box:
        warnings.simplefilter("always")
        assert old_thing(21) == 42          # 行为不变（弃用不是改语义）
    assert len(box) == 1
    rec = box[0]
    assert issubclass(rec.category, DeprecationWarning)
    msg = str(rec.message)
    assert "old_thing" in msg and "0.4.0" in msg and "new_thing()" in msg and "0.6.0" in msg
    # 元数据不丢（文档生成/inspect 不该因为弃用而看不见它）
    assert old_thing.__name__ == "old_thing"
    assert old_thing.__doc__ == "旧接口。"
    assert old_thing.__deprecated__["remove_in"] == "0.6.0"


def test_warning_points_at_the_caller():
    """`stacklevel` 要指到**调用方**，否则 `-W error` 报的位置永远是库内部，查不动。"""
    def deprecated_fn():
        dep.warn("old_fn", new="new_fn", since="0.4.0")

    with warnings.catch_warnings(record=True) as box:
        warnings.simplefilter("always")
        deprecated_fn()
    assert len(box) == 1
    assert box[0].filename == __file__, "warning 的 stacklevel 没指到测试文件（拿到 %r）" % (
        box[0].filename,)


def test_same_site_only_warns_once_per_process():
    """同一处每进程只响一次 —— 即使调用方把 filter 设成 always，也不许在循环里刷屏。"""
    def old_api():
        dep.warn("old_api", since="0.4.0")

    with warnings.catch_warnings(record=True) as box:
        warnings.simplefilter("always")
        for _ in range(5):
            old_api()
    assert len(box) == 1, "同一处响了 %d 次（应为 1）" % len(box)


def test_different_versions_of_same_name_are_separate_events():
    """同一个名字在不同版本各弃用一次 → 两次都应该响（键是「名字@since」）。"""
    with warnings.catch_warnings(record=True) as box:
        warnings.simplefilter("always")
        dep.warn("thing", since="0.4.0")
        dep.warn("thing", since="0.5.0")
    assert len(box) == 2


# ══════════════════════════════════════════════════════════════════════════
# 二、登记表：MIGRATIONS / DEPRECATED_API
# ══════════════════════════════════════════════════════════════════════════
def test_every_migration_entry_is_complete():
    """每条迁移都必须能回答四问：哪版变的、变了什么、旧代码怎么改、为什么。"""
    assert dep.MIGRATIONS, "迁移表为空？0.2→0.3 的破坏性变更必须登记"
    for key, ent in dep.MIGRATIONS.items():
        assert set(ent) == {"version", "change", "migration", "why"}, \
            "%s 的字段集不对：%s" % (key, sorted(ent))
        for field, text in ent.items():
            assert isinstance(text, str) and text.strip(), "%s.%s 是空的" % (key, field)
        parts = ent["version"].split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts), \
            "%s 的版本号不是 x.y.z：%r" % (key, ent["version"])


def test_the_known_breaking_changes_are_all_registered():
    """0.3.0 的四条破坏性变更必须都在表里（少一条 = 升级的人看不见）。"""
    keys = set(dep.MIGRATIONS)
    assert "meta-to-provenance" in keys        # ARCH-002 的核心
    assert "vol-unit-hand" in keys             # 0.2.0 的单位统一
    assert "non-price-series-rejected" in keys  # get_bars 拒绝 880005
    assert "concurrency-removed" in keys        # DEF-001 删功能
    versions = {k: v["version"] for k, v in dep.MIGRATIONS.items()}
    assert versions["meta-to-provenance"] == "0.3.0"
    assert versions["vol-unit-hand"] == "0.2.0"


def test_migration_table_renders_every_entry():
    """渲染出来的表：行数、每条变更/迁移文本都必须在里面（不许渲染漏项）。"""
    table = dep.as_markdown_table()
    lines = table.splitlines()
    assert lines[0].startswith("| 版本 |")
    assert lines[1].replace(" ", "") == "|---|---|---|---|"
    assert len(lines) == len(dep.MIGRATIONS) + 2
    for ent in dep.MIGRATIONS.values():
        assert ent["change"] in table
        assert ent["migration"] in table


def test_deprecated_api_registry_is_consistent():
    """「已声明弃用但仍可用」的登记：要么为空，要么每条都齐 four-field。"""
    for key, ent in dep.DEPRECATED_API.items():
        assert set(ent) >= {"since", "detail"}, "%s 缺字段：%s" % (key, sorted(ent))
        assert ent["since"].count(".") == 2, "%s 的 since 不是 x.y.z" % key
    text = dep.pending_markdown()
    assert text.splitlines()[0].startswith("| 旧接口 |")
    if not dep.DEPRECATED_API:
        assert "没有" in text


# ══════════════════════════════════════════════════════════════════════════
# 三、包级接线：版本元组 + 导出
# ══════════════════════════════════════════════════════════════════════════
def test_version_info_matches_version_string():
    assert isinstance(tdxdata.__version_info__, tuple)
    assert all(isinstance(p, int) for p in tdxdata.__version_info__)
    assert ".".join(str(p) for p in tdxdata.__version_info__) == tdxdata.__version__
    # 0.2 时代没有 __version_info__；这条保证它真实可用而不是摆设
    assert tdxdata.__version_info__ > (0, 2, 0)


def test_package_exports_the_mechanism():
    for name in ("deprecated", "MIGRATIONS", "DEPRECATED_API", "migration_table",
                 "__version_info__"):
        assert name in tdxdata.__all__, "%s 没有进 __all__" % name
        assert hasattr(tdxdata, name), "tdxdata.%s 取不到" % name
    assert tdxdata.migration_table() == dep.as_markdown_table()
