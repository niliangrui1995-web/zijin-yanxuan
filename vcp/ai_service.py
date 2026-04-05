# ai_service.py - AI 诊断服务（Kimi API 调用，内置联网搜索）
import json
import time
import threading
import logging

_log = logging.getLogger(__name__)

try:
    import requests
    from requests.adapters import HTTPAdapter
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

class KimiAIService:
    """封装 Kimi API 诊断调用，线程安全"""

    def __init__(self):
        self._session = None
        self._lock = threading.Lock()

    def _get_session(self):
        """懒初始化 requests.Session（线程安全）"""
        with self._lock:
            if self._session is None:
                if not REQUESTS_AVAILABLE:
                    return None
                self._session = requests.Session()
                try:
                    adapter = HTTPAdapter(pool_maxsize=4, max_retries=2)
                    self._session.mount('https://', adapter)
                except Exception:
                    pass
            return self._session

    def call_kimi_diag(self, api_key, code, name, session=None, diag_date=""):
        """调用 Kimi API 进行 AI 诊股，返回 (success, text)
        diag_date: 诊断日期字符串 (yyyy-MM-dd)，让 AI 基于该日期分析。
        """
        if not REQUESTS_AVAILABLE:
            return False, "requests 库未安装，AI 诊股不可用"

        if session is None:
            session = self._get_session()
        if session is None:
            return False, "无法创建 HTTP 会话"

        # 如果指定了诊断日期，注入到 prompt 中
        date_context = ""
        if diag_date:
            date_context = f"（请以 {diag_date} 为基准日期进行分析，检索该日期前后的相关信息）"

        system_prompt = (
            "你是 A 股利空利好梳理助手。联网搜索后按固定格式输出，每条内容必须明确标注「利空」或「利好」。"
            "有信息时：每条类别尽量列出 2～5 条，每条写清「日期 利空/利好 具体事实」（可 1～2 句话说明事件与影响）。"
            "无信息时该条写「无」。不要加标题符号（###），不要重复类别名。"
        )
        user_content = (
            f"标的：{name}（{code}）{date_context}。请联网检索后，直接按 1. 2. 3. 4. 四条回复，不要写「未来1个月潜在利好」等类别名称。\n\n"
            "顺序对应：1=未来1个月潜在利好 2=未来1个月潜在利空 3=近1个月利空 4=近1个月利好。\n\n"
            "格式：每条以「1.」「2.」「3.」「4.」开头，后接该类别下多条内容（每条换行，格式：日期 利空/利好 具体内容）；有则尽量多列 2～5 条并写清事实与影响，无则写「无」。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        payload = {
            "model": "moonshot-v1-32k",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 3072,
            "tools": [{"type": "builtin_function", "function": {"name": "$web_search"}}],
        }
        url = "https://api.moonshot.cn/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key}
        timeout = (10, 120)  # 连接10s + 读取120s（联网搜索较慢，VPN增加延迟）
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            t0 = time.time()
            try:
                _log.info(f"[AI诊断] 请求 Kimi: {name}({code}) 第{attempt+1}次...")
                resp = session.post(url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code != 200:
                    try:
                        err = resp.json()
                        msg = (err.get("error", {}).get("message", resp.text) or resp.text or str(resp.status_code)).lower()
                        last_error = f"Kimi 请求失败: {err.get('error', {}).get('message', resp.text)}"
                    except Exception as e:
                        msg = resp.text.lower() if resp.text else str(resp.status_code)
                        last_error = f"Kimi 请求失败: {str(e)}"
                        _log.error(f"[AI诊断] 响应解析失败: {str(e)}")
                    if ("overloaded" in msg or "try again" in msg or "429" in str(resp.status_code)) and attempt < max_retries - 1:
                        time.sleep(min(2 ** attempt * 2, 30))
                        continue
                    return False, last_error
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                msg_obj = choice.get("message", {})
                text = (msg_obj.get("content") or "").strip()
                tool_calls = msg_obj.get("tool_calls") or []
                if not text and tool_calls:
                    messages.append(msg_obj)
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        args_str = fn.get("arguments") or "{}"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "name": fn.get("name", "$web_search"),
                            "content": args_str,
                        })
                    payload2 = {"model": "moonshot-v1-32k", "messages": messages, "temperature": 0.2, "max_tokens": 3072}
                    data2 = None
                    for retry in range(max_retries):
                        try:
                            resp2 = session.post(url, json=payload2, headers=headers, timeout=timeout)
                            if resp2.status_code != 200:
                                try:
                                    err_body = resp2.text
                                    err_msg = (json.loads(err_body).get("error", {}).get("message", err_body) or err_body or str(resp2.status_code)).lower()
                                except Exception as e:
                                    err_msg = (resp2.text or str(resp2.status_code)).lower()
                                    _log.error(f"[AI诊断] 二次请求解析失败: {str(e)}")
                                if ("overloaded" in err_msg or "try again" in err_msg or "429" in str(resp2.status_code)) and retry < max_retries - 1:
                                    time.sleep(min(2 ** retry * 2, 30))
                                    continue
                                last_error = f"Kimi 二次请求失败: {resp2.text or resp2.status_code}"
                                return False, last_error
                            data2 = resp2.json()
                            break
                        except Exception as e2:
                            _log.error(f"[AI诊断] 联网搜索回调异常: {str(e2)}")
                            if retry < max_retries - 1:
                                time.sleep(min(2 ** retry * 2, 30))
                                continue
                            return False, f"Kimi 联网搜索回调异常: {repr(e2)}"
                    if data2 is None:
                        return False, last_error or "Kimi 联网搜索回调未返回数据"
                    text = ((data2.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
                if not text:
                    return False, "Kimi 接口返回内容为空"
                elapsed_total = time.time() - t0
                _log.info(f"[AI诊断] ✅ {name}({code}) 成功 (耗时 {elapsed_total:.1f}s, {len(text)} 字)")
                return True, text
            except requests.exceptions.RequestException as e:
                last_error = f"Kimi 请求异常: {repr(e)}"
                _log.error(f"[AI诊断] 请求异常: {str(e)}")
                if ("429" in str(e) or "overloaded" in str(e).lower() or "try again" in str(e).lower()) and attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt * 2, 30))
                    continue
                return False, last_error
            except Exception as e:
                last_error = f"Kimi 请求异常: {repr(e)}"
                _log.error(f"[AI诊断] 未知异常: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt * 2, 30))
                    continue
                return False, last_error
        return False, last_error or "Kimi 请求失败"

    def close(self):
        """关闭 HTTP 会话"""
        try:
            if self._session is not None:
                self._session.close()
                self._session = None
        except Exception:
            pass
