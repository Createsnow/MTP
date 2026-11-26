"""跨实例互斥：MySQL 命名锁（GET_LOCK），每把锁占一条独立连接，持锁连接断开时服务端自动释放。

命名锁在 MySQL 里是整台服务器共用的、不分库：同一台 MySQL 上的开发、测试、正式库（sn_dev / sn_test / sn_prod）
如果锁名相同，一个环境在同步订单、生成某张 PI 时，别的环境也被挡住（页面显示「同步进行中」、PI 忙）。所以锁名都带上
当前库名（lock_name），每个库各用各的锁；同一个库的多个进程、多个实例之间照常互斥。

- PI 操作锁（5.2.9 第 1 条）：同一 PI 的生成（含取消）、撤销生成、分配、转单、转组织确认（含直接转移）
  同一时刻只执行一个，后到的立即提示稍后再试（不排队）。后台生成把锁交给后台线程，结束时释放。
- 组织建批锁（F-12 取批规则第 6 条）：MES 取批时查未确认批次与建批在同一把锁内，并发取批不会各建一批。
- 订单同步锁（F-05 同步方式第 6 条）：各种同步共用；按单据同步、全量重建最多排队 30 秒，比对同步和进页同步直接跳过。
- 前缀锁：判断前缀能否用、占用前缀（见 rules.check_prefix）与用这个前缀生成（见 generate._generate_in_tx）
  都锁到事务结束，同一前缀串行：共用前缀的几张 PI 不会同时按同一个最大号接号。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import pymysql

from app.core.errors import ErrorCode, biz
from app.db.session import db


def lock_name(kind: str, key: str = "", database: str | None = None) -> str:
    """命名锁的锁名：sn_<kind>_ + md5(库名 / 键)。带库名：同一台 MySQL 上不同的库互不影响（见文首）。
    命名锁最长 64 字符：库名与键做摘要。database 不传为当前连接的库（db.target.database）。"""
    name = database if database is not None else db.target.database
    return f"sn_{kind}_" + hashlib.md5(f"{name}/{key}".encode()).hexdigest()


def _name(kind: str, key: str) -> str:
    return lock_name(kind, key)


class Held:
    """已持有的一组命名锁（一条独立连接）；release 可重复调用。"""

    def __init__(self, conn: pymysql.connections.Connection, names: list[str]) -> None:
        self._conn = conn
        self._names = names
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            with self._conn.cursor() as c:
                for n in reversed(self._names):
                    c.execute("SELECT RELEASE_LOCK(%s)", (n,))
        except pymysql.err.Error:
            pass
        finally:
            with suppress(Exception):
                self._conn.close()


def try_acquire(kind: str, keys: list[str], timeout: int = 0) -> Held | None:
    """按键排序依次加锁（避免死锁）；任一把拿不到则全部释放并返回 None。"""
    names = [_name(kind, k) for k in sorted(set(keys))]
    conn = db.connect_raw()
    got: list[str] = []
    try:
        with conn.cursor() as c:
            for n in names:
                c.execute("SELECT GET_LOCK(%s, %s) AS ok", (n, timeout))
                if (c.fetchone() or {}).get("ok") != 1:
                    Held(conn, got).release()
                    return None
                got.append(n)
    except BaseException:
        Held(conn, got).release()
        raise
    return Held(conn, got)


def pi_lock(*pis: str) -> Held:
    """拿 PI 操作锁；拿不到抛 PI_BUSY。调用方负责 release（或用 with_pi_lock）。"""
    keys = [p for p in pis if p]
    h = try_acquire("pi", keys)
    if h is None:
        raise biz(ErrorCode.PI_BUSY, pi="、".join(sorted(set(keys))))
    return h


@contextmanager
def with_pi_lock(*pis: str) -> Iterator[None]:
    h = pi_lock(*pis)
    try:
        yield
    finally:
        h.release()


@contextmanager
def with_org_lock(org: str, timeout: int = 10) -> Iterator[None]:
    h = try_acquire("org", [org], timeout)
    if h is None:
        raise biz(ErrorCode.CONFLICT_RETRY)
    try:
        yield
    finally:
        h.release()


#: 前缀锁最长等待秒数（同一前缀正被另一笔生成 / 规则保存占用时，等它提交）
PREFIX_WAIT_SECONDS = 30


def hold_prefix(prefix: str) -> None:
    """前缀锁：同一前缀同一时刻只有一笔事务在判断、占用（新建 / 首次归属版本）或用它生成；锁到当前事务结束。
    不在事务里时不加（只判断、不写，没有要保护的）。等不到报稍后重试。"""
    hold_prefixes([prefix])


def hold_prefixes(prefixes) -> None:
    """一次拿几个前缀的前缀锁（按前缀排序依次拿，避免两笔事务交叉等待），锁到当前事务结束。
    同一事务里已拿过的前缀不再拿：命名锁各占一条独立连接，同一事务再拿同一把会等自己（见 rules.grow_version），
    生成事务开头已拿本次全部前缀，之后规则落库（rules.check_prefix）不会再等。
    不在事务里时不加。任一把等不到报稍后重试（已拿到的跟事务一起释放）。"""
    local = db.tx_local()
    if local is None:
        return
    held: set[str] = local.setdefault("prefix_locks", set())
    for p in sorted({p for p in prefixes if p} - held):
        h = try_acquire("prefix", [p], PREFIX_WAIT_SECONDS)
        if h is None:
            raise biz(ErrorCode.CONFLICT_RETRY)
        held.add(p)
        db.after_tx(h.release)


#: 按单据同步、全量重建等同步锁的最长秒数：须明显短于页面请求超时（120 秒），否则页面已报超时、后台仍排着队，
#: 拿到锁后照样执行；排队期间每个请求还占一个工作线程（与 MES 接口共用）和一条数据库连接
SYNC_WAIT_SECONDS = 30


def sync_running() -> bool:
    """订单同步锁是否有人持有（任一实例）。"""
    return db.scalar("SELECT IS_USED_LOCK(%s)", _name("sync", "orders")) is not None


def sync_lock(wait: bool) -> Held | None:
    """订单同步锁：wait = True 时最多等 SYNC_WAIT_SECONDS（按单据同步、全量重建排队），否则拿不到返回 None。"""
    return try_acquire("sync", ["orders"], SYNC_WAIT_SECONDS if wait else 0)


# ------------------------------------------------------------------ 前缀入场（防两个互为开头的前缀同时进入使用）
#
# 前缀锁只锁「完全相同」的前缀：AB 与 ABC 是两把锁。两笔生成各自首次用 AB、ABC 时，长事务里互相看不到对方
# 还没提交的规则版本，「不能一个是另一个的开头」的判断可能都通过。前缀入场把这类判断排成一队：
# - 全局入场锁（PREFIX_ENTRY）只在判断时短暂持有（生成事务）或持有到短事务结束（选规则、规则库保存）；
# - 生成事务在入场锁内把自己正在用的前缀登记为「在途」（sn_prefix_claim，独立短事务提交），事务结束删掉；
#   在途登记只在它的前缀锁仍被持有时有效（进程退出、连接断开即自动失效，不会留下卡死的登记）；
# - 后来的判断碰上与自己互为开头的在途前缀，先放开入场锁、等对方的前缀锁（对方提交或回滚），再重新判断：
#   结果与对方先提交、再判断时完全相同（原有规则不变），只是不会再两边都放行。

#: 入场锁（全局一把）
PREFIX_ENTRY = "prefixentry"


def held_prefix_keys() -> set[str]:
    """本事务已持有的前缀锁键（同 hold_prefixes）；不在事务里为空。"""
    local = db.tx_local()
    return set() if local is None else set(local.get("prefix_locks", set()))


def prefix_key_used(key: str) -> bool:
    """有人（任一实例、任一连接）持有这个前缀锁。"""
    return db.scalar("SELECT IS_USED_LOCK(%s)", _name("prefix", key)) is not None


def wait_prefix_key(key: str, timeout: float) -> bool:
    """等别人放开这个前缀锁（拿到即放开，不持有）；timeout 秒内等到返回 True。
    MySQL 判定命名锁死锁（ER_USER_LOCK_DEADLOCK）时返回 False（调用方按稍后重试处理）。"""
    try:
        h = try_acquire("prefix", [key], max(1, int(timeout + 0.999)))
    except pymysql.err.OperationalError:
        return False
    if h is None:
        return False
    h.release()
    return True


def entry_lock(timeout: float) -> Held | None:
    """拿入场锁（最多等 timeout 秒）；拿不到返回 None。"""
    return try_acquire(PREFIX_ENTRY, ["all"], max(1, int(timeout + 0.999)))


def hold_entry_lock(timeout: float) -> bool:
    """入场锁持有到当前事务结束（本事务已持有时不再拿）；拿不到返回 False。不在事务里时不加、返回 True。"""
    local = db.tx_local()
    if local is None or local.get("entry_lock"):
        return True
    h = entry_lock(timeout)
    if h is None:
        return False
    local["entry_lock"] = True
    db.after_tx(h.release)
    return True


def entry_held() -> bool:
    """本事务持有入场锁到事务结束。"""
    local = db.tx_local()
    return bool(local and local.get("entry_lock"))
