-- 防重加固（2026-11-26）：全公司 SN 唯一登记、前缀入场在途登记。只加表、补数据，不改已有的表与数据。

-- 全公司 SN 唯一登记：第一 SN（号池 sn_item，含 SN 导入的号）与第二 SN（sn_item_sub）共用一个主键，
-- 两张表之间撞号也由数据库拦下（原来两表各有唯一键、互相管不到）。生成、导入写号时同事务登记，删号、导入整单删除时
-- 同事务移除；转单、转组织不改 SN，不动这张表
CREATE TABLE sn_registry (
  sn    VARCHAR(64) NOT NULL COMMENT '完整 SN（第一 SN 或第二 SN）',
  kind  TINYINT     NOT NULL COMMENT '1 第一 SN（sn_item）/ 2 第二 SN（sn_item_sub）',
  PRIMARY KEY (sn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='全公司 SN 唯一登记（第一 SN 与第二 SN 共用）';

-- 现有的号补登记：第一 SN 在号池里本就唯一；第二 SN 若与第一 SN 已有相同的（历史数据问题）跳过，
-- 可用运维手册里的核对语句查出
INSERT INTO sn_registry(sn, kind) SELECT sn, 1 FROM sn_item;
INSERT INTO sn_registry(sn, kind) SELECT s.sn2, 2 FROM sn_item_sub s LEFT JOIN sn_registry r ON r.sn = s.sn2 WHERE r.sn IS NULL;

-- 前缀入场在途登记：生成事务正在用、还没提交的前缀（见 locks 的「前缀入场」）。生成事务结束即删；
-- 只在该前缀的前缀锁仍被持有时有效（进程退出即失效，下次碰到时删掉）
CREATE TABLE sn_prefix_claim (
  prefix      VARCHAR(64) NOT NULL COMMENT '前缀（识别码为前缀层文本）',
  kind        CHAR(1)     NOT NULL COMMENT 'P 普通前缀 / I 识别码前缀层',
  pi_no       VARCHAR(64) NOT NULL COMMENT '正在用它生成的 PI',
  created_at  DATETIME    NOT NULL,
  PRIMARY KEY (prefix, kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='前缀入场在途登记';
