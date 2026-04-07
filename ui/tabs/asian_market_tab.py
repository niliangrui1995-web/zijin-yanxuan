# -*- coding: utf-8 -*-
import os
import json
import datetime
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableView, QHeaderView, QPushButton, QLabel, QCheckBox, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components.vcp_table_view import VCPTableView

# ==================== 黑魔法：全局劫持发包器 ====================
# ⚠️ WARNING: 此 monkey-patch 会影响整个进程中所有使用 requests/curl_cffi 的模块！
# 仅劫持 query1/query2.finance.yahoo.com 两个域名，不影响其它 URL。
# 如需扩展标的源，请改用独立 Session 方案替代全局劫持。
GLOBAL_USE_CF_PROXY = True
_CF_HIJACK_DOMAINS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")

def _apply_url_rewrite(*args, **kwargs):
    if GLOBAL_USE_CF_PROXY:
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if isinstance(url, str):
            for domain in _CF_HIJACK_DOMAINS:
                if domain in url:
                    url = url.replace(domain, "yf.niliangrui.cloud")
            if len(args) > 1:
                args = (args[0], url) + args[2:]
            else:
                kwargs["url"] = url
    return args, kwargs

import requests
_old_req_request = requests.Session.request
def _cf_hijack_req(self, *args, **kwargs):
    args, kwargs = _apply_url_rewrite(*args, **kwargs)
    return _old_req_request(self, *args, **kwargs)
requests.Session.request = _cf_hijack_req

try:
    import curl_cffi.requests
    _old_curl_request = curl_cffi.requests.Session.request
    def _cf_hijack_curl(self, *args, **kwargs):
        args, kwargs = _apply_url_rewrite(*args, **kwargs)
        return _old_curl_request(self, *args, **kwargs)
    curl_cffi.requests.Session.request = _cf_hijack_curl
except ImportError:
    pass
# ================================================================

import yfinance as yf

from ui.tabs.base_stock_tab import BaseStockTab
from core.event_bus import event_bus
from core.logger import get_logger

log = get_logger(__name__)

from vcp.constants import CACHE_DIR
JSON_CACHE = os.path.join(CACHE_DIR, "asian_klines_latest.json")
RT_JSON_CACHE = os.path.join(CACHE_DIR, "asian_rt_latest.json")

# Global dict to store the realtime prices and today's mini-df for kline patching
GLOBAL_ASIAN_RT_CACHE = {}

def get_track_mapping():
    # 彻底覆盖底层 JSON 解析中因为注释空行换行等引起的断档漏网之鱼
    return {
        '3661.TW': '定制化ASIC与代工',
        '3711.TW': '先进封装与混合键合',
        '3037.TW': '核心基板与封装材料',
        '8046.TW': '核心基板与封装材料',
        '2383.TW': '高频PCB与覆铜板材料',
        '6213.TW': '高频PCB与覆铜板材料',
        '1303.TW': '核心基板与封装材料',
        '2313.TW': '高频PCB与覆铜板材料',
        '3044.TW': '高频PCB与覆铜板材料',
        '2308.TW': 'AI服务器基建与温控',
        '3324.TWO': 'AI服务器基建与温控',
        '3017.TW': 'AI服务器基建与温控',
        '2449.TW': '测试/老化与探针卡',
        '6223.TWO': '测试/老化与探针卡',
        '2454.TW': '边缘AI与具身智能',
        '8035.T': '晶圆制造与材料设备',
        '4062.T': '核心基板与封装材料',
        '2802.T': '核心基板与封装材料',
        '5802.T': '光模块/光纤与CPO',  
        '6752.T': '高频PCB与覆铜板材料',
        '3110.T': '高频PCB与覆铜板材料',
        '3407.T': '高频PCB与覆铜板材料',
        '4004.T': '高频PCB与覆铜板材料',
        '4182.T': '高频PCB与覆铜板材料',
        '5706.T': '高频PCB与覆铜板材料',
        '5801.T': '高频PCB与覆铜板材料',
        '5201.T': 'AI服务器基建与温控',
        '6594.T': 'AI服务器基建与温控',
        '6857.T': '测试/老化与探针卡',
        '6146.T': '测试/老化与探针卡',
        '000660.KS': 'HBM与核心存储矩阵',
        '005930.KS': 'HBM与核心存储矩阵',
        '042700.KS': 'HBM与核心存储矩阵',
        '009150.KS': '核心基板与封装材料',
        '000150.KS': '高频PCB与覆铜板材料',
        '0522.HK': '先进封装与混合键合',
    }

def get_role_mapping():
    # 结合之前 Markdown 文件提炼的高品质角色定位，我们作为第一梯队直接嵌入，
    # 确保像 Alchip(3661) 这种在字典里没写备注的标的也能获得精美注释。
    roles_mapping = {
        '3661.TW': 'ASIC设计服务龙头',
        '3711.TW': '全球封测龙头',
        '3037.TW': 'ABF载板双寡头之一',
        '8046.TW': '转型AI服务器载板',
        '2383.TW': '高频CCL龙头',
        '6213.TW': 'M9级CCL核心',
        '1303.TW': 'CCL/PP大宗供应',
        '2313.TW': '高阶HDI/PCB',
        '3044.TW': '车用PCB龙头',
        '2308.TW': '服务器电源+散热',
        '3324.TWO': 'GPU散热模组龙头',
        '3017.TW': '散热模组/热管',
        '2449.TW': '晶圆测试代工龙头',
        '6223.TWO': 'RF探针卡/高频测试',
        '2454.TW': '边缘AI芯片巨头',
        
        '8035.T': '光刻后道设备霸主',
        '4062.T': 'ABF载板双寡头之一',
        '2802.T': 'ABF绝缘膜独家供应',
        '5802.T': '光纤+光模块上游核心',
        '6752.T': 'MEGTRON高频CCL',
        '3110.T': 'T-Glass全球垂直垄断',
        '3407.T': '石英布/电子材料',
        '4004.T': 'CCL核心供应',
        '4182.T': 'BT树脂核心供应',
        '5706.T': '极低轮廓铜箔寡头',
        '5801.T': 'HVLP4铜箔双寡头',
        '5201.T': '氟化液冷+CPO材料',
        '6594.T': '液冷循环泵+精密马达',
        '6857.T': 'ATE测试设备龙头',
        '6146.T': '划片机全球垄断',
        
        '000660.KS': 'HBM绝对龙头',
        '005930.KS': '存储+代工+封装全能',
        '042700.KS': 'HBM TBonder垄断',
        '009150.KS': '载板+MLCC',
        '000150.KS': '韩系CCL寡头',
        
        '0522.HK': '先进封装设备龙头',
    }
    
    # 依然保留从 industry_dict.py 动态读取的能力，作为后续您添加新股票的兜底映射
    dict_path = r"D:\vcp_hunter\每日战报\每日战报\industry_dict.py"
    if not os.path.exists(dict_path):
        return roles_mapping
    try:
        with open(dict_path, 'r', encoding='utf-8') as f:
            for line in f:
                if "#" in line and (".T\"" in line or ".TW\"" in line or ".TWO\"" in line or ".KS\"" in line or ".HK\"" in line):
                    import re
                    m = re.search(r'\"([A-Z0-9\.]+)\"', line)
                    if m:
                        code = m.group(1)
                        if code not in roles_mapping: # 只在此处补充，不覆盖上面精心雕琢的高质量标签
                            comment = line.split('#')[-1].strip()
                            m_role = re.search(r'[(（](.*?)[)）]', comment)
                            if m_role:
                                comment = m_role.group(1).strip()
                            roles_mapping[code] = comment
    except Exception as e:
        log.error(f"[AsianTab] 解析角色字典失败: {e}")
    return roles_mapping


def _get_ch_names_mapping() -> dict:
    return {
        '2330.TW': '台积电',
        '2317.TW': '鸿海',
        '2454.TW': '联发科',
        '2308.TW': '台达电',
        '2382.TW': '广达',
        '3231.TW': '纬创',
        '2356.TW': '英业达',
        '3017.TW': '双鸿',
        '3324.TWO': '奇鋐',
        '5201.T': '旭硝子',
        '6594.T': '日本电产',
        '000660.KS': 'SK海力士',
        '005930.KS': '三星电子',
        '2449.TW': '京元电子',
        '6223.TWO': '旺矽',
        '6857.T': '爱德万测试',
        '6146.T': '迪思科',
        '3661.TW': '世芯',
        '8035.T': '东京电子',
        '3044.TW': '健鼎',
        '2383.TW': '台光电',
        '6213.TW': '联茂',
        '2313.TW': '华通',
        '3407.T': '旭化成',
        '5801.T': '古河电工',
        '4182.T': '三菱瓦斯化学',
        '5706.T': '三井金属',
        '3110.T': '日东纺',
        '6752.T': '松下',
        '4004.T': '力森诺科',
        '000150.KS': '斗山',
        '5802.T': '住友电工',
        '1303.TW': '南亚塑胶',
        '8046.TW': '南亚电路板',
        '3037.TW': '欣兴',
        '2802.T': '味之素',
        '042700.KS': '韩美半导体',
        '3711.TW': '日月光',
        '009150.KS': '三星电机',
        '4062.T': '揖斐电'
    }


def get_market_status(market: str) -> str:
    import datetime
    now_utc = datetime.datetime.utcnow()
    
    if market in ['T', 'KS']:  # Japan/Korea UTC+9
        local_now = now_utc + datetime.timedelta(hours=9)
    else: # TW, TWO, HK UTC+8
        local_now = now_utc + datetime.timedelta(hours=8)
        
    today_str = local_now.strftime("%Y-%m-%d")
    KNOWN_HOLIDAYS_2026 = {
        'HK': ['2026-04-03', '2026-04-04', '2026-04-06', '2026-04-07', '2026-05-01', '2026-05-25', '2026-07-01'],
        'TW': ['2026-04-03', '2026-04-04', '2026-04-05', '2026-04-06', '2026-05-01', '2026-06-19'],
        'T':  ['2026-04-29', '2026-05-03', '2026-05-04', '2026-05-05', '2026-05-06', '2026-07-20', '2026-08-11'],
        'KS': ['2026-03-01', '2026-04-10', '2026-05-05', '2026-05-15', '2026-06-06', '2026-08-15']
    }
    
    if market in KNOWN_HOLIDAYS_2026 and today_str in KNOWN_HOLIDAYS_2026[market]:
        return "🔴 休市"
        
    if local_now.weekday() >= 5:
        return "🔴 休市"
        
    time_num = local_now.hour * 100 + local_now.minute
    
    if market == 'T':
        if (900 <= time_num <= 1130) or (1230 <= time_num <= 1500): return "🟢 交易中"
        elif 1130 < time_num < 1230: return "🟡 午休"
    elif market == 'KS':
        if 900 <= time_num <= 1530: return "🟢 交易中"
    elif market in ['TW', 'TWO']:
        if 900 <= time_num <= 1330: return "🟢 交易中"
    elif market == 'HK':
        if (930 <= time_num <= 1200) or (1300 <= time_num <= 1600): return "🟢 交易中"
        elif 1200 < time_num < 1300: return "🟡 午休"
            
    return "🔴 休市"


class AsianMarketWorker(QThread):
    progress = pyqtSignal(str)
    result_ready = pyqtSignal(dict)

    def __init__(self, codes):
        super().__init__()
        self.codes = codes
        self._is_running = True

    def stop(self):
        self._is_running = False
        
    def trigger_refresh(self):
        self._force_refresh = True

    def run(self):
        import time
        while self._is_running:
            now = datetime.datetime.now()
            
            # 【智能休眠机制】：非交易时段（周末、或每日 16:35 至次日 08:00）静默挂机，节省资源，除非用户手动点击了刷新
            # 为何是 16:35？港股 16:00 收盘，YF 有 15~20 分钟延迟，必须继续向后轮询获取最后的收盘价
            time_num = now.hour * 100 + now.minute
            is_trading_hours = (now.weekday() < 5) and (800 <= time_num <= 1635)
            is_manual_refresh = getattr(self, '_force_refresh', False)
            
            if not is_trading_hours and not is_manual_refresh:
                self.progress.emit("🌙 休市休眠中 (按刷新键可强拉)...")
                time.sleep(1)
                continue
            try:
                self.progress.emit(f"[{now.strftime('%H:%M:%S')}] 拉取最新报价中...")
                updates = {}
                
                # We use ThreadPool to do this ultra fast 
                
                def _fetch(code):
                    ticker = yf.Ticker(code)
                    fast_info = ticker.fast_info
                    df = ticker.history(period="2mo", interval="1d")
                    if not df.empty:
                        # 优先使用 fast_info 中的实时价格，如果为空再退化到 df
                        close_price = float(fast_info.get("lastPrice") or df.iloc[-1]['Close'])
                        day_open = float(fast_info.get("open") or df.iloc[-1]['Open'])
                        day_high = float(fast_info.get("dayHigh") or df.iloc[-1]['High'])
                        day_low = float(fast_info.get("dayLow") or df.iloc[-1]['Low'])
                        
                        prev_close = float(fast_info.get("previousClose", 0))
                        
                        if prev_close <= 0 and 'Open' in df:
                            prev_close = float(df.iloc[-1]['Open'])
                            
                        pct = 0.0
                        if prev_close > 0:
                            pct = ((close_price / prev_close) - 1.0) * 100.0
                            
                        def get_past_pct(days_ago):
                            if len(df) > days_ago:
                                past_close = float(df.iloc[-(days_ago + 1)]['Close'])
                                if past_close > 0:
                                    return ((close_price / past_close) - 1.0) * 100.0
                            return 0.0

                        pct_5 = get_past_pct(5)
                        pct_10 = get_past_pct(10)
                        pct_20 = get_past_pct(20)
                            
                        currency = fast_info.get('currency', 'USD')
                        
                        # Store in global cache so main kline window can access it rapidly
                        GLOBAL_ASIAN_RT_CACHE[code] = {
                            "close": close_price,
                            "open": day_open,
                            "high": day_high,
                            "low": day_low,
                            "pct": pct,
                            "pct_5": pct_5,
                            "pct_10": pct_10,
                            "pct_20": pct_20,
                            "currency": currency,
                            "df_today": df  
                        }
                        
                        return code, GLOBAL_ASIAN_RT_CACHE[code]
                    return code, None
                
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {executor.submit(_fetch, code): code for code in self.codes}
                    for future in concurrent.futures.as_completed(futures):
                        if not self._is_running:
                            break
                        res_code, res_data = future.result()
                        if res_data:
                            updates[res_code] = res_data
                        
                if self._is_running and updates:
                    self.result_ready.emit(updates)
                    ok_msg = f"✅ [{datetime.datetime.now().strftime('%H:%M:%S')}] 亚洲市场 YF 外网接口直连成功 (获取 {len(updates)} 支最新报价)"
                    self.progress.emit(ok_msg)
                    log.info(f"[AsianTab] {ok_msg}")
                
            except Exception as e:
                err_str = str(e)
                # 增加更友好的网络与VPN识别
                if "Too Many Requests" in err_str or "Rate limited" in err_str or "429" in err_str:
                    err_hint = "Yahoo接口被限流(429)！如果开启了VPN请尝试切换节点；如果没开请开启VPN。"
                elif "Timeout" in err_str or "Max retries" in err_str or "unreachable" in err_str.lower() or "Connection" in err_str:
                    err_hint = "连接YF失败！外网接口严重依赖梯子，请检查 VPN 是否已开启（建议开启全局模式）。"
                elif "NoneType" in err_str and "subscriptable" in err_str:
                    err_hint = "请求代理/CF隧道遇到墙阻断或空响应，未能获取合法数据，请尝试开启VPN全局代理并关闭CF节点。"
                else:
                    err_hint = f"YF拉取遭遇异常: {err_str}"
                    
                msg = f"❌ 外网断开: {err_hint}"
                self.progress.emit(msg)
                log.error(f"[AsianTab] {msg} | Native Error: {e}")

            # 休眠 120 秒（2分钟），支持外部中断或手动刷新
            for _ in range(120 * 10):
                if not self._is_running:
                    return
                if getattr(self, '_force_refresh', False):
                    self._force_refresh = False
                    break
                time.sleep(0.1)


class AsianCacheFetcherThread(QThread):
    finished_sig = pyqtSignal(bool, str)
    
    def run(self):
        try:
            from vcp.fetchers.asian_kline_fetcher import fetch_all_asian_klines, save_kline_data
            
            # 使用略低于盘中的并发数跑静态缓存
            data = fetch_all_asian_klines(max_workers=4)
            if data:
                save_kline_data(data)
                self.finished_sig.emit(True, "✅ 16:30 盘后自动同步完成！")
            else:
                self.finished_sig.emit(False, "❌ 盘后缓存全量拉取失败")
        except Exception as e:
            self.finished_sig.emit(False, f"❌ 盘后拉取异常: {e}")


class AsianMarketTab(BaseStockTab):
    """亚洲寡头行情面板"""
    def __init__(self, data_provider=None, parent=None):
        super().__init__(data_provider, parent)
        self._init_ui()
        
        # 1. 冷开机瞬间加载本地 JSON (asian_klines_latest.json)
        self._load_local_cache()
        
        # 2. 启动后台 Worker, 进行 60 秒常态轮询
        codes = [item['代码'] for item in self.row_data]
        self.worker = AsianMarketWorker(codes)
        self.worker.progress.connect(self.lbl_status.setText)
        self.worker.result_ready.connect(self._on_rt_update)
        # 等界面加载完稍微延后一点启动后台
        QTimer.singleShot(1000, self.worker.start)
        
        # 3. 监听全局数据更新事件 (如被 deferred_load 静默更新完毕)
        event_bus.sig_asian_klines_ready.connect(self._on_asian_klines_ready)
        
        # 4. 自动缓存校验器：每分钟检查本地缓存是否需要更新
        self.auto_cache_timer = QTimer(self)
        self.auto_cache_timer.timeout.connect(self._check_auto_cache)
        self.auto_cache_timer.start(60000)
        QTimer.singleShot(2000, self._check_auto_cache)

    def _check_auto_cache(self):
        import os
        from datetime import datetime, timedelta
        if getattr(self, '_is_fetching_cache', False):
            return
            
        now = datetime.now()
        
        # 寻找距离当前时间最近的“上一次收盘清算节点 (工作日 16:30)”
        target_dt = now.replace(hour=16, minute=30, second=0, microsecond=0)
        
        if now < target_dt:
            # 如果今天还没到 16:30，目标基准点后退一天
            target_dt -= timedelta(days=1)
            
        # 确保基准点不能落在周末（遇到周六周日，持续往前后退直到周五）
        while target_dt.weekday() >= 5:
            target_dt -= timedelta(days=1)
            
        mtime = 0
        if os.path.exists(JSON_CACHE):
            mtime = os.path.getmtime(JSON_CACHE)
        
        cache_dt = datetime.fromtimestamp(mtime)
        
        # 如果冰柜里的文件比“最近的一次回波锚点”还要老，说明必须去拉新货了
        if cache_dt < target_dt:
            self._is_fetching_cache = True
            log.info(f"[AsianTab] 发现陈旧的 K 线缓存 (生成于 {cache_dt.strftime('%m-%d %H:%M')})，准备拉取 {target_dt.strftime('%m-%d %H:%M')} 后的数据补缺...")
            self.lbl_status.setText("⏳ 正在自动同步 16:30 收盘后最新 K线缓存...")
            self.cache_thread = AsianCacheFetcherThread()
            self.cache_thread.finished_sig.connect(self._on_auto_cache_finished)
            self.cache_thread.start()
                
    def _on_auto_cache_finished(self, success, msg):
        self._is_fetching_cache = False
        self.lbl_status.setText(msg)
        if success:
            self._load_local_cache()
            log.info("[AsianTab] 自动离线更新完成，已重载本地 K 线数据")

    def _on_asian_klines_ready(self):
        self._load_local_cache()
        self.lbl_status.setText("✅ 亚洲市场后台静默更新已就绪，K线已应用最新数据")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        header = QHBoxLayout()
        header.setContentsMargins(8, 6, 8, 6)
        
        title = QLabel("🌏 亚洲寡头核心资产监控 (由于存在日韩台港多股市，按 YF 实时接口为准)")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #E5E7EB;")
        self.lbl_status = QLabel("系统初始化...")
        self.lbl_status.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        
        self.chk_cf_proxy = QCheckBox("🚀 启用免翻墙直连 (CF隧道)")
        self.chk_cf_proxy.setToolTip("打勾：关闭VPN彻底裸连；不打勾：走您的VPN全局模式直连")
        self.chk_cf_proxy.setStyleSheet("color: #10B981; font-weight: bold; font-size: 13px; margin-right: 15px;")
        self.chk_cf_proxy.setChecked(True)
        self.chk_cf_proxy.toggled.connect(self._on_cf_proxy_toggled)
        
        self.btn_refresh = QPushButton("🔄 网络检查与手动刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setToolTip("强制跳过等待，立刻请求外网(Yahoo Finance)测速并获取最新价格")
        self.btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #374151; color: #E5E7EB; 
                border: 1px solid #4B5563; border-radius: 4px; 
                padding: 4px 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4B5563; border-color: #6B7280; }
            QPushButton:pressed { background-color: #1F2937; }
        """)
        self.btn_refresh.clicked.connect(self._on_manual_refresh)
        
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.lbl_status)
        header.addStretch()
        header.addWidget(self.chk_cf_proxy)
        header.addWidget(self.btn_refresh)
        
        layout.addLayout(header)

        self.asian_table = VCPTableView(default_row_height=28)
        layout.addWidget(self.asian_table)
        
        self.header_labels = ["代码", "名称", "现价", "涨幅%", "市场", "状态", "赛道", "角色定位", "货币", "5日涨跌%", "10日涨跌%", "20日涨跌%"]
        
        self.model = StockTableModel(self.header_labels)
        self.proxy_model = RtSortFilterProxyModel(self.asian_table)
        self.proxy_model.setSourceModel(self.model)
        self.asian_table.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.asian_table)
        self.asian_table.setItemDelegate(self.delegate)

        # Context menu
        self.asian_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.asian_table.customContextMenuRequested.connect(self._show_context_menu)
        
        # Double click to open Kline
        self.asian_table.doubleClicked.connect(self._on_double_click)

        # 列宽自定义并持久化
        header_view = self.asian_table.horizontalHeader()
        header_view.setStretchLastSection(False)
        
        default_widths = [70, 140, 90, 90, 80, 80, 120, 250, 60, 80, 80, 80]
        for i, w in enumerate(default_widths):
            header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.asian_table.setColumnWidth(i, w)
            
        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.asian_table, "header_state_asian_v2")

    def _show_context_menu(self, pos):
        index = self.asian_table.indexAt(pos)
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu
            build_stock_context_menu(self.asian_table, code, name)

    def _on_cf_proxy_toggled(self, checked):
        global GLOBAL_USE_CF_PROXY
        GLOBAL_USE_CF_PROXY = checked
        if hasattr(self, 'lbl_status'):
            self.lbl_status.setText(f"🔌 {'已切换为 CF 免翻墙专线' if checked else '已切换为 VPN 本地直连'}，下次刷新生效")

    def _on_manual_refresh(self):
        """手动触发外网数据更新"""
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.lbl_status.setText("⏳ 强制唤醒：正在请求海外接口测速与重载...")
            self.worker.trigger_refresh()
        else:
            self.lbl_status.setText("⚠ 后台网关未连接或已断开")

    def _load_local_cache(self):
        self.row_data = []
        if os.path.exists(JSON_CACHE):
            try:
                with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                    
                roles_map = get_role_mapping()
                ch_names_map = _get_ch_names_mapping()
                stocks_list = raw.get('stocks', [])
                    
                for item in stocks_list:
                    code = item.get('ticker')
                    if not code: continue
                    data_points = item.get('klines', [])
                    close_val = 0.0
                    pct_val = 0.0
                    
                    if len(data_points) >= 2:
                        close_val = float(data_points[-1].get('close', 0))
                        prev_close = float(data_points[-2].get('close', 0))
                        if prev_close > 0:
                            pct_val = ((close_val / prev_close) - 1.0) * 100.0
                        
                    # 计算 5/10/20 日涨幅时防御除零（停牌或数据异常时 close 可能为 0）
                    def _safe_pct(cur, ref_val):
                        return ((cur / ref_val) - 1.0) * 100.0 if ref_val > 0 and cur > 0 else 0.0

                    if len(data_points) >= 6: pct_5 = _safe_pct(close_val, float(data_points[-6].get('close', 0)))
                    else: pct_5 = 0.0
                    if len(data_points) >= 11: pct_10 = _safe_pct(close_val, float(data_points[-11].get('close', 0)))
                    else: pct_10 = 0.0
                    if len(data_points) >= 21: pct_20 = _safe_pct(close_val, float(data_points[-21].get('close', 0)))
                    else: pct_20 = 0.0
                    
                    role_desc = roles_map.get(code, item.get('name', ''))
                    
                    mkt_str = item.get('market', code.split('.')[-1] if '.' in code else '')
                    # 禁用 Emoji 避免 Qt Windows C++ Access Violation 崩溃
                    flags = {"T": "JP", "TW": "TW", "TWO": "TW", "KS": "KR", "HK": "HK"}
                    prefix = flags.get(mkt_str, "GLB")
                    mkt_val = f"{prefix} {mkt_str}"
                    
                    # 取最后一天所在的 df 填入 globals (为了让它打开 K 线图时不会空)
                    if code not in GLOBAL_ASIAN_RT_CACHE:
                        GLOBAL_ASIAN_RT_CACHE[code] = {
                            "close": close_val,
                            "pct": pct_val,
                            "pct_5": pct_5,
                            "pct_10": pct_10,
                            "pct_20": pct_20,
                            "currency": item.get('currency', ''),
                            "df_today": None # 历史回切时先无细节 K 线
                        }
                    
                    # 初始化时直接计算当前确切的盘中/休市状态
                    real_status = get_market_status(code.split('.')[-1] if '.' in code else '')
                    
                    cv = float(close_val) if close_val else 0.0
                    fmt_close = f"{cv:.3f}" if 0 < cv < 10 else (f"{cv:.2f}" if cv > 0 else "--")
                    
                    row_obj = {
                        "代码": code,
                        "名称": f"{item.get('name', '')}  ({ch_names_map.get(code, '未录入')})" if ch_names_map.get(code) else item.get('name', ''),
                        "现价": fmt_close,
                        "涨幅%": pct_val,
                        "市场": mkt_val,
                        "状态": real_status, 
                        "赛道": item.get('track', ''),
                        "角色定位": role_desc,
                        "货币": item.get('currency', '---'),
                        "5日涨跌%": pct_5,
                        "10日涨跌%": pct_10,
                        "20日涨跌%": pct_20
                    }
                    self.row_data.append(row_obj)
            except Exception as e:
                log.error(f"[AsianTab] JSON 历史缓存加载失败: {e}")

        # --- 恢复退出时的最后一次盘口实时缓存 ---
        if os.path.exists(RT_JSON_CACHE):
            try:
                with open(RT_JSON_CACHE, 'r', encoding='utf-8') as f:
                    rt_cache = json.load(f)
                for row_dict in self.row_data:
                    code = row_dict.get("代码")
                    if code in rt_cache:
                        info = rt_cache[code]
                        cv = float(info.get('close', 0.0))
                        row_dict["现价"] = f"{cv:.3f}" if 0 < cv < 10 else (f"{cv:.2f}" if cv > 0 else "--")
                        row_dict["涨幅%"] = info.get('pct', 0.0)
                        row_dict["5日涨跌%"] = info.get('pct_5', 0.0)
                        row_dict["10日涨跌%"] = info.get('pct_10', 0.0)
                        row_dict["20日涨跌%"] = info.get('pct_20', 0.0)
                        if info.get('currency'):
                            row_dict["货币"] = info['currency']
                        
                        if code not in GLOBAL_ASIAN_RT_CACHE:
                            GLOBAL_ASIAN_RT_CACHE[code] = {}
                        GLOBAL_ASIAN_RT_CACHE[code].update(info)
            except Exception as e:
                log.error(f"[AsianTab] 恢复 RT 盘口缓存失败: {e}")
                
        self.update_table_ui()

    def update_table_ui(self):
        self.model.update_data(self.row_data)

    def _on_rt_update(self, updates: dict):
        """Worker 传回最新报价时无损更新界面，保持排序和滚动条位置"""
        for row_idx, row_dict in enumerate(self.model.row_data):
            code = row_dict.get("代码")
            if code in updates:
                info = updates[code]
                mkt = code.split('.')[-1] if '.' in code else ''
                row_dict["状态"] = get_market_status(mkt)
                
                cv = float(info['close']) if info['close'] else 0.0
                row_dict["现价"] = f"{cv:.3f}" if 0 < cv < 10 else (f"{cv:.2f}" if cv > 0 else "--")
                
                row_dict["涨幅%"] = info['pct']
                row_dict["5日涨跌%"] = info.get('pct_5', 0.0)
                row_dict["10日涨跌%"] = info.get('pct_10', 0.0)
                row_dict["20日涨跌%"] = info.get('pct_20', 0.0)
                row_dict["货币"] = info['currency']
                
                # trigger row update
                self.model.dataChanged.emit(
                    self.model.index(row_idx, 0),
                    self.model.index(row_idx, len(self.model._headers)-1)
                )
        
        self._save_rt_cache()

    def _save_rt_cache(self):
        try:
            cache_friendly = {}
            for k, v in GLOBAL_ASIAN_RT_CACHE.items():
                cache_friendly[k] = {
                    "close": v.get("close", 0.0),
                    "pct": v.get("pct", 0.0),
                    "pct_5": v.get("pct_5", 0.0),
                    "pct_10": v.get("pct_10", 0.0),
                    "pct_20": v.get("pct_20", 0.0),
                    "currency": v.get("currency", "")
                }
            with open(RT_JSON_CACHE, 'w', encoding='utf-8') as f:
                json.dump(cache_friendly, f, ensure_ascii=False)
        except Exception as e:
            log.error(f"[AsianTab] 持久化 RT 缓存失败: {e}")

    def _on_double_click(self, index):
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        # 按当前表格视觉排序顺序构建列表，让 K 线窗口的"上一只/下一只"跟随用户排序
        code_list = []
        for r in range(self.proxy_model.rowCount()):
            s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
            if s_idx.row() < len(self.model.row_data):
                rd = self.model.row_data[s_idx.row()]
                code_list.append({'代码': rd.get("代码", ""), '名称': rd.get("名称", "")})
        
        current_idx = 0
        for i, c in enumerate(code_list):
            if c['代码'] == code:
                current_idx = i
                break
                
        # 触发全局画图事件
        event_bus.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def closeEvent(self, event):
        if hasattr(self, 'worker'):
            self.worker.stop()
            self.worker.wait(3000)  # 3 秒超时，防止卡死
        super().closeEvent(event)
