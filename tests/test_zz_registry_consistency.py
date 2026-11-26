"""放在最后跑（文件名排序）：整套测试跑完后，全公司 SN 唯一登记与号池、第二 SN 完全一致——
生成、导入、删号、导入整单删除、转单、转组织等写号路径都同步维护了登记，没有漏登、也没有残留。"""

from __future__ import annotations


def test_registry_matches_pool(sql):
    missing1 = sql.count(
        "SELECT COUNT(*) FROM sn_item i LEFT JOIN sn_registry r ON r.sn = i.sn AND r.kind = 1 WHERE r.sn IS NULL"
    )
    missing2 = sql.count(
        "SELECT COUNT(*) FROM sn_item_sub s LEFT JOIN sn_registry r ON r.sn = s.sn2 AND r.kind = 2 WHERE r.sn IS NULL"
    )
    stale = sql.count(
        "SELECT COUNT(*) FROM sn_registry r WHERE NOT ("
        "(r.kind = 1 AND EXISTS (SELECT 1 FROM sn_item i WHERE i.sn = r.sn)) OR "
        "(r.kind = 2 AND EXISTS (SELECT 1 FROM sn_item_sub s WHERE s.sn2 = r.sn)))"
    )
    assert (missing1, missing2, stale) == (0, 0, 0)
    assert sql.count("SELECT COUNT(*) FROM sn_registry") == sql.count("SELECT COUNT(*) FROM sn_item") + sql.count(
        "SELECT COUNT(*) FROM sn_item_sub"
    )
