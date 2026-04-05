# constants.py - 全局常量、配色、字体、默认参数
# 从 vcp_hunter.pyw 提取，零逻辑变更
import os
import warnings

warnings.filterwarnings('ignore')

# ==========================================
# 脚本目录
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
# 项目根目录（vcp/ 的上一层）
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# ==========================================
# 主界面字体等比例放大（约 1.2 倍），供全局使用
# ==========================================
UI_FONT_SMALL = 11   # 状态栏等
UI_FONT = 12         # 主界面正文
UI_FONT_MED = 13     # 小节标题
UI_FONT_TITLE = 19   # 大标题
UI_FONT_LOG = 14     # 日志/等宽

# ==========================================
# K 线图统一配色：深蓝底 + 高对比，彭博终端专业风格
# ==========================================
CHART_BG = '#0A0E17'
CHART_PANEL = '#0F1621'
CHART_GRID = '#1E293B'
CHART_UP = '#EF4444'
CHART_DN = '#22D3EE'
CHART_ACCENT = '#3B82F6'
CHART_FG = '#F1F5F9'
CHART_FG_SEC = '#94A3B8'
CHART_WARN = '#F59E0B'
CHART_SUCCESS = '#10B981'



# ==========================================
# 日期格式常量
# ==========================================
DATE_FMT = '%Y%m%d'

# ==========================================
# A 股交易时间
# ==========================================
MARKET_OPEN_AM, MARKET_CLOSE_AM = (9, 25), (11, 30)
MARKET_OPEN_PM, MARKET_CLOSE_PM = (13, 0), (15, 0)

# ==========================================
# 版本号（统一管理，闪屏和状态栏引用此常量）
APP_VERSION = "4.0.1"

# 数据目录
# ==========================================
def get_data_dir(sub_folder="Cache"):
    """数据目录统一放在项目根目录的 data/ 下，所有缓存、导出文件集中管理"""
    base = os.path.join(PROJECT_ROOT, 'data')
    target_dir = os.path.join(base, sub_folder)
    os.makedirs(target_dir, exist_ok=True)
    return target_dir

CACHE_DIR  = get_data_dir("Cache")
EXPORT_DIR = get_data_dir("Export")

# AI 诊断配置
AI_DIAG_CONFIG_PATH = os.path.join(CACHE_DIR, "ai_diag_config.json")
AI_DIAG_CACHE_RT = os.path.join(CACHE_DIR, "ai_diag_rt.json")
AI_DIAG_CACHE_SPECIAL = os.path.join(CACHE_DIR, "ai_diag_special.json")
SPECIAL_POOL_DATA_CACHE = os.path.join(CACHE_DIR, "vcp_special_pool_data.pkl")
RPS_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_rps_precomputed.pkl")        # F5预算RPS矩阵
SECTOR_RPS_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_sector_rps_precomputed.pkl")  # F5预算板块RPS
SHAREHOLDER_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_shareholder_cache.pkl")       # 十大流通股东缓存
FINANCE_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_finance_cache.pkl")               # 财务股本缓存(防服务器断连)
SPECIAL_LATEST_DATA = os.path.join(PROJECT_ROOT, 'data', 'special_latest_data.json')  # 关注池最新数据
MIN_MARKET_CAP = 4e9    # 最低总市值门槛：40亿元

# ==========================================
# 机构投资者类型定义（用于十大流通股东筛选）
# 股东性质包含以下关键词之一即视为机构
# ==========================================
INSTITUTION_KEYWORDS = ('基金', '券商', '保险', '信托', '社保', 'QFII')
# 股东名称包含以下关键词也视为机构（北向资金等）
INSTITUTION_NAME_KEYWORDS = ('香港中央结算', '中国证券金融', '中央汇金')


# ==========================================
# 默认策略参数
# ==========================================
DEFAULT_AMP_THRESHOLD = 0.45   # 左区区间振幅上限，默认不超过 45%

# ==========================================
# 全局运行/缓存配置常量
# ==========================================
CACHE_VERSION       = 3      # 本地缓存结构版本号
RT_CACHE_VERSION    = 2      # 盘中监控缓存结构版本号
MAX_HISTORY_BARS    = 500    # 全量历史下载长度
INCREMENTAL_BARS    = 30     # 增量更新长度
MARKET_SYNC_WORKERS = 15     # 同步市场数据线程数
RPS_BUFFER_DAYS     = 500    # 预留自然日数

# ==========================================
# 三高点区间参数（130日窗口）
# ==========================================
LOOKBACK_DAYS           = 130
GROUP_DAYS              = 15
PEAKS_FROM_GROUPS       = 5
PCT_BASELINE            = 0.93
MERGE_WITHIN_DAYS       = 15
EXCLUDE_DAYS_FOR_PEAKS  = 3
MIN_DAYS_BETWEEN_PEAKS  = 10

# ==========================================
# 弹性区间参数
# ==========================================
MIN_PEAKS_COUNT         = 3
MAX_PEAKS_COUNT         = 4
FLEXIBLE_MIN_INTERVAL   = 30
FLEXIBLE_MAX_INTERVAL   = 150
MIN_DAYS_AFTER_LAST_PEAK = 2
MIN_DAYS_AFTER_LAST_PEAK_CONFIRM = 3
MAX_R2_BELOW_R1_PCT     = 0.15
MIN_FIRST_TO_THIRD_DAYS = 50
MIN_R1_R2_DAYS         = 50

# ==========================================
# RPS稳定性参数
# ==========================================
RPS_STABILITY_DAYS      = 5
RPS_STABILITY_THRESHOLD = 80

# ==========================================
# 均线斜率参数
# ==========================================
MIN_SMA50_SLOPE         = 0.001

