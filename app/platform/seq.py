"""单号序列（sn_seq，底座的表；阶段 4c 由 SN 交付代码原样搬来，语句与事务用法不变）。

一行一个名字：批次号的每日序号（PS- / PL- / XO-…）、只用来加行锁的互斥行（# 开头，如 #push_claim）、
每日维护完成时间（#daily_maintain，见 partitions）。表结构不变。
"""

from __future__ import annotations

from app.db.session import db


def ensure(name: str) -> None:
    """序号行不在才插入（val = 0）。先不加锁地看在不在：「INSERT … ON DUPLICATE KEY UPDATE」碰上已有的行
    会连带锁住它前面的间隙。"""
    if not db.exists("SELECT EXISTS(SELECT 1 FROM sn_seq WHERE name = %s)", name):
        db.exec("INSERT IGNORE INTO sn_seq(name, val) VALUES (%s, 0)", name)


def bump(name: str) -> int:
    """序号加 1 并返回新值；在调用方的事务内执行（调用方负责独立短事务与死锁重试）。"""
    ensure(name)
    db.exec("UPDATE sn_seq SET val = val + 1 WHERE name = %s", name)
    return int(db.scalar("SELECT val FROM sn_seq WHERE name = %s", name))


def lock(name: str) -> None:
    """对序号行加排他锁到事务结束（互斥行用）。"""
    db.one("SELECT val FROM sn_seq WHERE name = %s FOR UPDATE", name)
