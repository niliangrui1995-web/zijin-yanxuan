# -*- coding: utf-8 -*-
"""
ui/theme.py
紫金研选 — 集中式主题色彩常量文件
所有 UI 组件应引用此文件中的常量，避免散落硬编码
"""


# ============================================================
# 背景色层级体系（深 → 浅）
# ============================================================
BG_CANVAS = "#0F1117"        # 主画布背景
BG_SIDEBAR = "#161B26"       # 侧栏 / Tab 栏
BG_TABLE_ALT = "#161B26"     # 表格交替行
BG_CARD = "#1A1F2E"          # 卡片 / 面板
BG_HOVER = "#1C212B"         # 悬浮 / 选中态
BG_INPUT = "#0D1117"         # 输入框 / 控件内部
BG_ELEVATED = "#252A36"      # 弹出层 / 高亮控件

# ============================================================
# 文字色
# ============================================================
TEXT_PRIMARY = "#E2E8F0"      # 主文字（亮白）
TEXT_SECONDARY = "#A0AEC0"    # 辅助文字（灰白）
TEXT_MUTED = "#718096"        # 弱文字（暗灰）
TEXT_DISABLED = "#4B5563"     # 禁用态文字

# ============================================================
# 品牌色
# ============================================================
BRAND_PRIMARY = "#8B5CF6"     # 品牌紫
BRAND_HOVER = "#A78BFA"       # 品牌紫 hover
BRAND_DEEP = "#6D28D9"        # 品牌紫深色
BRAND_SUBTLE = "rgba(139, 92, 246, 0.12)"  # 品牌紫低饱和度

# ============================================================
# 功能色 — 涨跌
# ============================================================
COLOR_RISE = "#FC8181"         # 涨 / 红色（正常涨幅）
COLOR_RISE_STRONG = "#E85D5D" # 涨 / 红色（强涨 >5%）
COLOR_FALL = "#68D391"         # 跌 / 绿色（正常跌幅）
COLOR_FALL_STRONG = "#3CC68A"  # 跌 / 绿色（强跌 <-5%）
COLOR_FLAT = "#C9CDD4"        # 平盘 / 中性

# ============================================================
# 功能色 — 状态
# ============================================================
COLOR_SUCCESS = "#10B981"      # 成功 / 在线
COLOR_WARNING = "#F59E0B"      # 警告 / 黄色
COLOR_ERROR = "#EF4444"        # 错误 / 红色
COLOR_INFO = "#3B82F6"         # 信息 / 蓝色

# ============================================================
# 边框色
# ============================================================
BORDER_DEFAULT = "rgba(255, 255, 255, 0.05)"
BORDER_SUBTLE = "rgba(255, 255, 255, 0.03)"
BORDER_STRONG = "#374151"
BORDER_BRAND = "rgba(139, 92, 246, 0.15)"

# ============================================================
# 评分着色梯度
# ============================================================
SCORE_EXCELLENT = "#FF4757"    # ≥90 分 红色粗体
SCORE_GOOD = "#F59E0B"         # ≥80 分 橙色
SCORE_NORMAL = "#3A82F6"       # ≥60 分 蓝色
SCORE_LOW = "#8E8E93"          # <60 分 灰色

# ============================================================
# 突破状态着色
# ============================================================
STATUS_BREAKOUT = "#E85D5D"    # 放量突破 → 红色粗体
STATUS_APPROACHING = "#FFD60A" # 临近突破 → 黄色
STATUS_VCP = "#3A82F6"         # VCP 蓄力 → 蓝色
STATUS_INACTIVE = "#8E8E93"    # 观望 / 异常 → 灰色

# ============================================================
# 字体
# ============================================================
FONT_FAMILY = '"Microsoft YaHei UI", "Inter", "Segoe UI", sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'
FONT_SIZE_NORMAL = 13
FONT_SIZE_SMALL = 12
FONT_SIZE_HEADER = 11
FONT_SIZE_TITLE = 15


def apply_rise_fall_color(pct_val: float) -> str:
    """根据涨跌幅返回颜色字符串"""
    if pct_val > 5:
        return COLOR_RISE_STRONG
    elif pct_val > 0:
        return COLOR_RISE
    elif pct_val < -5:
        return COLOR_FALL_STRONG
    elif pct_val < 0:
        return COLOR_FALL
    return COLOR_FLAT


def apply_score_color(score_val: float) -> str:
    """根据评分返回颜色字符串"""
    if score_val >= 90:
        return SCORE_EXCELLENT
    elif score_val >= 80:
        return SCORE_GOOD
    elif score_val >= 60:
        return SCORE_NORMAL
    return SCORE_LOW
