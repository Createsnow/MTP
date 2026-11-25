"""MES 接口契约（V1.3，已与 MES 联调通过，冻结）：本系统发给 MES 的报文、MES 调本系统接口的应答，结构与基准逐项一致。

基准文件 tests/contract/mes_v1.json 由联调通过的代码生成，记录每个场景的：
- HTTP 状态码、code、schemaVersion、batchType 等枚举值；
- 报文结构：字段名与顺序、每个字段的类型（str / int / bool / null / 对象 / 数组），数组取第一个元素；
- 推送请求头：头名称与固定值（认证方式、压缩、Content-Type）。

不比较批次号、SN、时间等每次不同的值，也不比较 message 文案（MES 不按文案判断）。

**这个测试失败 = 改动影响了 MES 接口。** 先确认是否误改；确需变更接口时，须先与 MES 方确认、升级《MES 对接接口文档》
版本号，再用下面的命令重新生成基准，并在提交说明里写明接口变化：

    SN_UPDATE_MES_CONTRACT=1 uv run pytest tests/test_mes_contract.py
"""

from __future__ import annotations

import difflib
import gzip
import json
import os
from pathlib import Path
from typing import Any

from conftest import MES, Res, m, ok, uid

GOLDEN = Path(__file__).parent / "contract" / "mes_v1.json"

#: 报文里按值比较的字段（枚举、版本号）；其余字段只比较类型
ENUM_FIELDS = {"schemaVersion", "code", "batchType", "batchResult", "status", "field"}

#: 推送请求头里按值比较的头；其余头（X-Request-Id、时间戳、签名）只记录存在
FIXED_HEADERS = {"content-type", "accept", "accept-encoding", "content-encoding", "x-sn-attempt"}


def shape(v: Any, key: str | None = None) -> Any:
    """报文结构：字段名与顺序 + 类型；枚举字段记录取值。"""
    if isinstance(v, dict):
        return {k: shape(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    if key in ENUM_FIELDS and isinstance(v, str):
        return f"= {v}"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"


def response(r: Res) -> dict:
    return {"status": r.status, "body": shape(r.body)}


def push_headers(h: dict) -> dict:
    out = {}
    for k in sorted(h):
        if k in FIXED_HEADERS:
            out[k] = h[k]
        elif k == "authorization":
            out[k] = h[k].split(" ", 1)[0] + " <令牌>"
        elif k.startswith("x-sn-") or k == "x-request-id":
            out[k] = "<有值>"
    return out


def pushed(org: str, batch_type: str) -> dict:
    x = next(x for x in reversed(MES.received) if x["org"] == org and x["batch"]["batchType"] == batch_type)
    return x


def setup_pi(w, qty: int, batch_size: int = 1000, auth: str = "TOKEN", gzip_on: bool = False):
    org = w.org(uid("O")[:10])
    w.enable_mes(org, batch_size=batch_size, auth=auth)
    if gzip_on:
        cfg = m(mes_url=MES.url(org), mes_auth_type=auth, mes_gzip=True)  # 改压缩须先连接测试
        assert ok(w.api.post(f"/api/factories/{org}/mes-test", cfg, w.admin))["ok"]
        ok(w.api.put(f"/api/factories/{org}/mes-config", cfg, w.admin))
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, ("M1", qty, "0100"))
    ok(w.bind_pi(pi, w.rule("K" + uid("").replace("-", ""), seq_len=6)))
    w.generate(pi)
    return org, pi, bill


def collect(w, sql) -> dict:
    got: dict[str, Any] = {}

    # ---------------- ① 推送：连接测试、交付批次（Bearer 令牌，原始 JSON）
    org, pi, bill = setup_pi(w, qty=3)
    ping = pushed(org, "PING")
    got["①推送-连接测试 PING"] = {"headers": push_headers(ping["headers"]), "body": shape(ping["batch"])}
    w.allocate(pi)
    w.push()
    new = pushed(org, "NEW")
    got["①推送-交付批次 NEW（Bearer 令牌）"] = {"headers": push_headers(new["headers"]), "body": shape(new["batch"])}

    # ---------------- ① 推送：HMAC 签名 + gzip
    horg, hpi, _ = setup_pi(w, qty=2, auth="HMAC", gzip_on=True)
    w.allocate(hpi)
    w.push()
    h = pushed(horg, "NEW")
    got["①推送-交付批次 NEW（HMAC 签名、gzip）"] = {
        "headers": push_headers(h["headers"]),
        "body": shape(h["batch"]),
    }

    # ---------------- ① 推送：转组织（来源收 TRANSFER_OUT，目标收 TRANSFER_IN）
    b = w.org(uid("O")[:10])
    w.enable_mes(b)
    body = m(
        factory_code=org,
        pi_no=pi,
        bill_no=bill,
        scope="BILL",
        to_factory=b,
        to_pi=pi,
        to_bill=bill,
        to_material="M1",
        reason="契约测试",
    )
    ok(w.api.post("/api/transfers/org/direct", body, w.admin))
    w.push()
    got["①推送-转出批次 TRANSFER_OUT"] = {"body": shape(pushed(org, "TRANSFER_OUT")["batch"])}
    got["①推送-转入批次 TRANSFER_IN"] = {"body": shape(pushed(b, "TRANSFER_IN")["batch"])}

    # ---------------- ① 推送：双 SN（明细 sn2 有值）
    dorg = w.org(uid("O")[:10])
    w.enable_mes(dorg)
    dpi, dbill = uid("PI"), uid("MO")
    w.order(dbill, dorg, "C1", dpi, ("M1", 2))
    ok(w.api.post("/api/generate/dual", m(pi_no=dpi, dual=True), w.admin))
    for slot, prefix in ((1, "D" + uid("").replace("-", "")), (2, "E" + uid("").replace("-", ""))):
        custom = m(prefix=prefix, base=10, seq_len=4, with_length=False)
        sel = m(pi_no=dpi, lines=[m(bill_no=dbill, seq=1)], custom=custom, slot=slot)
        ok(w.api.post("/api/rules/line-select", sel, w.admin))
    w.generate(dpi)
    w.allocate(dpi)
    w.push()
    got["①推送-交付批次 NEW（双 SN）"] = {"body": shape(pushed(dorg, "NEW")["batch"])}

    # ---------------- ② 取下一批 / ③ 回执（MES 接口账户）
    porg, ppi, _ = setup_pi(w, qty=7, batch_size=5)
    MES.behavior[porg] = lambda batch, h: ("drop", None)  # 推送一直断连，批次留给 MES 来取
    w.allocate(ppi)
    w.push()
    token, _ = w.mes_account(porg)
    r = w.mes_next(token)
    got["②取下一批-未确认的推送批次"] = response(r)
    batch = r["batch"]
    got["②取下一批-再取（同一批）"] = response(w.mes_next(token))
    got["②取下一批-按批次号取回"] = response(w.mes_next(token, batchNo=batch["batchNo"]))
    receipt = MES.process(porg, batch)
    got["③回执-参数不合法"] = response(w.mes_receipt(token, {**receipt, "code": "PARTIAL"}))
    got["③回执-缺批次号"] = response(w.mes_receipt(token, {"code": "OK"}))
    got["③回执-批次不存在"] = response(w.mes_receipt(token, {**receipt, "batchNo": batch["batchNo"].lower()}))
    raw = gzip.compress(json.dumps(receipt).encode("utf-8"))
    hh = {"Content-Type": "application/json", "Content-Encoding": "gzip", "Authorization": f"Bearer {token}"}
    x = w.api.client.post("/api/mes/v1/receipts", content=raw, headers=hh)
    got["③回执-不支持 gzip"] = {"status": x.status_code, "body": shape(x.json())}
    got["③回执-首次确认 OK"] = response(w.mes_receipt(token, receipt))
    got["③回执-重复提交"] = response(w.mes_receipt(token, receipt))
    got["②取下一批-已确认的批次号"] = response(w.mes_next(token, batchNo=batch["batchNo"]))
    r = w.mes_next(token, pi=ppi)
    got["②取下一批-新建拉取批次 PL-"] = response(r)
    ok(w.mes_receipt(token, MES.process(porg, r["batch"])))
    got["②取下一批-没有可取的"] = response(w.mes_next(token))
    got["②取下一批-参数类型不对"] = response(w.mes_next(token, pi=1))

    # ---------------- ④ 已交付的号 / ⑤ 批次里的号（只读）
    got["④已交付的号-分页（有下一页）"] = response(w.mes_delivered(token, pi=ppi, pageSize=2))
    got["④已交付的号-参数类型不对"] = response(w.mes_delivered(token, cursor=10))
    got["⑤批次里的号-分页（有下一页）"] = response(w.mes_items(token, batch["batchNo"], pageSize=2))
    got["⑤批次里的号-缺批次号"] = response(w.api.post("/api/mes/v1/batches/items", {}, token))
    got["⑤批次里的号-批次不存在"] = response(w.mes_items(token, "PS-NOT-EXISTS"))

    # ---------------- 认证与权限
    got["认证-不带 Authorization"] = response(w.api.post("/api/mes/v1/batches/next", m(schemaVersion="1.0"), None))
    got["认证-密钥不对"] = response(w.mes_next(token.split(":")[0] + ":wrong-secret"))
    got["权限-页面账户调 ②"] = response(w.mes_next(w.admin))
    big = w.api.client.post(
        "/api/mes/v1/sn/delivered",
        content=json.dumps({"pi": ppi}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"},
    )
    got["应答压缩-超过 1 KB 按 gzip 返回"] = {
        "status": big.status_code,
        "content-encoding": big.headers.get("content-encoding"),
    }
    return got


def test_mes_contract_unchanged(w, sql):
    got = collect(w, sql)
    text = json.dumps(got, ensure_ascii=False, indent=2) + "\n"
    if os.environ.get("SN_UPDATE_MES_CONTRACT") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    want = GOLDEN.read_text(encoding="utf-8")
    if text != want:
        diff = "".join(difflib.unified_diff(want.splitlines(True), text.splitlines(True), "基准", "现在", n=2))
        raise AssertionError("MES 接口报文与联调通过的基准不一致（见本文件开头的说明）：\n" + diff)
