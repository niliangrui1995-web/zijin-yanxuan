# QSettings Key Registry

## 说明

自 2026-04-20 起，持久化配置统一经 `infra/settings` 和 `core/app_config` 管理。任何新增 key 都必须：

1. 使用命名空间前缀；
2. 指定 owner；
3. 说明 legacy scope；
4. 在本表登记。

## 根级键

| Key | Owner | Source | Notes |
| --- | --- | --- | --- |
| `settings/schema_version` | `infra/settings` | `SettingsMigrator` | 仓储 schema 版本 |
| `scan/rps_threshold` | `core/app_config` | Scan settings | VCP RPS 阈值 |
| `scan/amp_threshold` | `core/app_config` | Scan settings | 波动压缩阈值 |
| `scan/ma_bind_threshold` | `core/app_config` | Scan settings | 均线粘合阈值 |
| `scan/min_amount` | `core/app_config` | Scan settings | 最小成交额阈值 |
| `scan/high_250_threshold` | `core/app_config` | Scan settings | 距 250 日高点阈值 |
| `network/offline_mode` | `core/app_config` | Main window | 网络离线模式 |

## `window/*`

Legacy scope: `MainWindowQT`

| Key | Owner | Notes |
| --- | --- | --- |
| `window/geometry` | `MainWindowQT` | 主窗口几何信息 |
| `window/state` | `MainWindowQT` | 主窗口状态 |
| `window/last_active_tab` | `MainWindowQT` | 上次激活 tab |
| `window/geometry_version` | `MainWindowQT` section store | 无边框窗口缓存版本 |

## `ui/*`

| Key | Owner | Legacy scope | Notes |
| --- | --- | --- | --- |
| `ui/table_density` | `ui/main_window_visuals.py` | none | 表格密度 |
| `ui/theme/current_theme` | `ui/theme.py` | `ThemeManager` | 当前主题 |
| `ui/theme/auto_switch_theme` | `ui/theme.py` | `ThemeManager` | 自动日夜切换 |

## `rt/*`

Legacy scope: `RtMonitorTab`

| Key | Owner | Notes |
| --- | --- | --- |
| `rt/interval` | `ui/tabs/rt_monitor_tab.py` | 监控轮询间隔 |
| `rt/rps` | `ui/tabs/rt_monitor_tab.py` | 盘中监控 RPS 阈值 |

## `scan/*`

Legacy scope: `ScanTab`

| Key | Owner | Notes |
| --- | --- | --- |
| `scan/rps_threshold` | `ui/tabs/scan_tab.py` via `app_config` | 扫描参数 |
| `scan/amp_threshold` | `ui/tabs/scan_tab.py` via `app_config` | 扫描参数 |
| `scan/ma_bind_threshold` | `ui/tabs/scan_tab.py` via `app_config` | 扫描参数 |
| `scan/min_amount` | `ui/tabs/scan_tab.py` via `app_config` | 扫描参数 |
| `scan/high_250_threshold` | `ui/tabs/scan_tab.py` via `app_config` | 扫描参数 |
| `scan/user_presets` | `ui/tabs/scan_tab.py` | 用户自定义预设 JSON |

## `tabs/FundHoldingsTab/*`

Legacy scope: `FundHoldingsTab`

| Key | Owner | Notes |
| --- | --- | --- |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/subject_names` | `ui/tabs/fund_holdings_tab.py` | 多选主体 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/subject_name` | `ui/tabs/fund_holdings_tab.py` | 单主体兼容键 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/capital_attributes` | `ui/tabs/fund_holdings_tab.py` | 资金属性筛选 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/search_text` | `ui/tabs/fund_holdings_tab.py` | 搜索关键字 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/quarter_mode` | `ui/tabs/fund_holdings_tab.py` | 季度模式 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/quarter_values` | `ui/tabs/fund_holdings_tab.py` | 季度值集合 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/change_types` | `ui/tabs/fund_holdings_tab.py` | 变动类型筛选 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/sort_column` | `ui/tabs/fund_holdings_tab.py` | 排序列 |
| `tabs/FundHoldingsTab/fund_holdings_view_state_v2/sort_order` | `ui/tabs/fund_holdings_tab.py` | 排序方向 |

## 变更流程

新增 key 前必须回答：

1. 这个 key 是否已有所属 section？
2. 是否需要 legacy migration？
3. 是否可以复用现有键而不是再造一个近义键？
4. 是否已经把 key 和记忆体现在测试与文档中？
