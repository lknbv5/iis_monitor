

import os
import subprocess
import threading
import time
import re
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional

import gradio as gr

# ============================================
# 配置常量
# ============================================
APP_CMD = r"%windir%\system32\inetsrv\appcmd.exe"
CONFIG_FILE = "iis_monitor_config.json"
LOG_FILE = "iis_monitor.log"
STATS_FILE = "iis_monitor_stats.json"
MAX_LOG_LINES = 200

# 自定义 CSS 字体样式
custom_css = """
/* 全局字体设置 */
body, gradio-app {
    font-family: "Microsoft YaHei", "PingFang SC", "Heiti SC", sans-serif !important;
}

/* 标题字体加粗 */
h1, h2, h3 {
    font-weight: 600 !important;
}

/* 优化 Markdown 渲染的字体 */
.markdown-body {
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif !important;
}

/* 表格表头字体 */
th {
    font-weight: bold !important;
}

/* 日志框使用等宽字体，方便查看对齐 */
textarea {
    font-family: "Consolas", "Monaco", "Courier New", monospace !important;
}
"""

# ============================================
# 数据类定义
# ============================================
@dataclass
class WebsiteConfig:
    # name: str
    url: str = ""
    enabled: bool = True
    check_timeout: int = 10
    expected_status: int = 200

@dataclass
class AppPoolConfig:
    # name: str
    enabled: bool = True
    auto_restart: bool = True
    restart_delay: int = 5

@dataclass
class MonitorStatus:
    status: str = "stopped"  # stopped, running, error
    start_time: Optional[str] = None
    last_check_time: Optional[str] = None
    total_checks: int = 0
    total_failures: int = 0
    total_restarts: int = 0
    uptime_seconds: float = 0

@dataclass
class StatsData:
    daily_checks: Dict[str, int] = field(default_factory=dict)
    daily_failures: Dict[str, int] = field(default_factory=dict)
    daily_restarts: Dict[str, int] = field(default_factory=dict)

# ============================================
# 工具函数
# ============================================
def get_today_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def format_duration(seconds: float) -> str:
    if seconds <= 0: return "0 秒"
    if seconds < 60: return f"{seconds:.1f} 秒"
    elif seconds < 3600: return f"{seconds / 60:.1f} 分钟"
    else: return f"{seconds / 3600:.1f} 小时"

def run_appcmd(*args) -> tuple[bool, str]:
    """执行 appcmd 命令，增强稳定性"""
    try:
        cmd = [APP_CMD] + list(args)
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            encoding='gbk', # 尝试使用GBK编码处理Windows输出
            errors='ignore'  # 忽略编码错误
        )
        # 如果GBK失败，尝试UTF-8
        if not result.stdout and result.stderr:
             result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='ignore')
        
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"
    except Exception as e:
        return False, str(e)

def get_iis_sites() -> List[Dict[str, any]]:
    success, output = run_appcmd("list", "site")
    if not success:
        print(f"获取网站失败: {output}")
        return []
    
    sites = []
    for line in output.strip().split('\n'):
        if not line.strip(): continue
        # 格式: SITE "Default Web Site" (id:1,bindings:http/*:80:,state:Started)
        match = re.match(r'SITE "([^"]+)" \(id:(\d+),bindings:([^)]+),state:(\w+)\)', line)
        if match:
            name, site_id, bindings, state = match.groups()
            sites.append({"name": name, "id": site_id, "bindings": bindings, "state": state})
    return sites

def get_iis_app_pools() -> List[Dict[str, any]]:
    success, output = run_appcmd("list", "apppool")
    if not success:
        print(f"获取应用池失败: {output}")
        return []
    
    app_pools = []
    for line in output.strip().split('\n'):
        if not line.strip(): continue
        match = re.match(r'APPPOOL "([^"]+)" \(MgdVersion:([^,]+),MgdMode:([^,]+),state:(\w+)\)', line)
        if match:
            name, version, mode, state = match.groups()
            app_pools.append({"name": name, "version": version, "mode": mode, "state": state})
        else:
            # 兼容性处理：如果标准正则不匹配，尝试简单提取
            simple_match = re.search(r'APPPOOL "([^"]+)"', line)
            if simple_match:
                name = simple_match.group(1)
                app_pools.append({"name": name, "version": "Unknown", "mode": "Unknown", "state": "Unknown"})
                
    return app_pools

def get_app_pool_state(app_pool_name: str) -> str:
    success, output = run_appcmd("list", "apppool", app_pool_name)
    if success and "Started" in output:
        return "Started"
    elif success and "Stopped" in output:
        return "Stopped"
    return "Unknown"

def restart_website(site_name: str, delay: int = 5) -> bool:
    """
    使用 appcmd 重启网站
    """
    try:
        # 先停止
        run_appcmd("stop", "site", site_name)
        # 等待
        time.sleep(delay)
        # 再启动
        success, _ = run_appcmd("start", "site", site_name)
        return success
    except Exception as e:
        print(f"重启网站 {site_name} 失败: {e}")
        return False

def restart_app_pool(app_pool_name: str, delay: int = 5) -> bool:
    try:
        run_appcmd("stop", "apppool", app_pool_name)
        time.sleep(delay)
        success, _ = run_appcmd("start", "apppool", app_pool_name)
        return success
    except Exception:
        return False

def get_site_bindings_url(site_name: str) -> Optional[str]:
    try:
        success, output = run_appcmd("list", "site", site_name)
        if not success: return None
        
        match = re.search(r'bindings:([^)]+)', output)
        if not match: return None
        
        bindings = match.group(1)
        http_match = re.search(r'http/([^:]+):(\d+):', bindings)
        if http_match:
            ip = http_match.group(1)
            port = http_match.group(2)
            hostname = "localhost" if ip == "*" or ip == "" else ip
            return f"http://{hostname}:{port}"
        return None
    except Exception:
        return None

def get_site_app_pool(site_name: str) -> Optional[str]:
    try:
        success, output = run_appcmd("list", "site", site_name)
        if not success: return None
        match = re.search(r'applicationPool:"([^"]+)"', output)
        if match: return match.group(1)
        return None
    except Exception:
        return None

# ============================================
# 日志与配置管理
# ============================================
class LogManager:
    def __init__(self, log_file: str = LOG_FILE):
        self.log_file = log_file
        self.memory_logs: List[str] = []
        self.lock = threading.Lock()
    
    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}"
        with self.lock:
            self.memory_logs.append(log_entry)
            if len(self.memory_logs) > MAX_LOG_LINES: self.memory_logs.pop(0)
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(log_entry + "\n")
            except: pass
        print(log_entry) # 控制台输出
    
    def get_recent_logs(self, count: int = 50) -> List[str]:
        with self.lock:
            return self.memory_logs[-count:] if len(self.memory_logs) > count else self.memory_logs.copy()

class ConfigManager:
    def __init__(self):
        self.config_file = CONFIG_FILE
        self.websites: Dict[str, WebsiteConfig] = {}
        self.app_pools: Dict[str, AppPoolConfig] = {}
        self.check_interval = 60
        self.max_failures = 3
        self.global_auto_restart = True
        self.load()
    
    def add_website(self, config: WebsiteConfig):
        self.websites = config
        self.save()
        
    def add_app_pool(self, config: AppPoolConfig):
        self.app_pools = config
        self.save()
    
    def save(self):
        try:
            data = {
                "websites": {k: asdict(v) for k, v in self.websites.items()},
                "app_pools": {k: asdict(v) for k, v in self.app_pools.items()},
                "check_interval": self.check_interval,
                "max_failures": self.max_failures,
                "global_auto_restart": self.global_auto_restart
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def load(self):
        try:
            if not os.path.exists(self.config_file): return
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.websites = {k: WebsiteConfig(**v) for k, v in data.get("websites", {}).items()}
            self.app_pools = {k: AppPoolConfig(**v) for k, v in data.get("app_pools", {}).items()}
            self.check_interval = data.get("check_interval", 60)
            self.max_failures = data.get("max_failures", 3)
            self.global_auto_restart = data.get("global_auto_restart", True)
        except Exception as e:
            print(f"加载配置失败: {e}")

# ============================================
# 监控核心类 
# ============================================
class IISMonitor:
    def __init__(self):
        self.config_manager = ConfigManager()
        self.logger = LogManager()
        
        # 线程控制
        self.running = False
        self.stop_event = threading.Event()
        self.monitor_thread: Optional[threading.Thread] = None
        self.lock = threading.RLock() # 使用可重入锁
        
        # 状态数据
        self.status = MonitorStatus()
        self.website_status: Dict[str, Dict] = {} # {name: {status, fail_count, last_check...}}
        self.apppool_status: Dict[str, Dict] = {}
        
        self._init_status()

    def _init_status(self):
        """初始化状态字典"""
        with self.lock:
            for name in self.config_manager.websites:
                if name not in self.website_status:
                    self.website_status[name] = {"status": "unknown", "fail_count": 0, "last_check": "-", "total_checks": 0}
            for name in self.config_manager.app_pools:
                if name not in self.apppool_status:
                    self.apppool_status[name] = {"status": "unknown", "fail_count": 0, "last_check": "-", "total_checks": 0}

    def start(self) -> tuple[bool, str]:
        if self.running:
            return False, "监控已在运行中"
        
        self.running = True
        self.stop_event.clear()
        
        # 重置状态
        self.status = MonitorStatus(
            status="running",
            start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        
        self._init_status()
        self.logger.log("监控已启动", "INFO")
        
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        return True, "监控已启动"

    def stop(self) -> tuple[bool, str]:
        if not self.running:
            return False, "监控未在运行"
        
        self.logger.log("正在停止监控...", "INFO")
        self.running = False # 标志位先置为False，防止循环继续
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        # 强制重置显示状态
        with self.lock:
            self.status.status = "stopped"
            self.status.uptime_seconds = 0
            self.status.start_time = None
            
        self.logger.log("监控已停止", "INFO")
        return True, "监控已停止"

    def _monitor_loop(self):
        while self.running and not self.stop_event.is_set():
            try:
                self._check_cycle()
                
                # 更新运行时长
                if self.status.start_time:
                    start_dt = datetime.strptime(self.status.start_time, "%Y-%m-%d %H:%M:%S")
                    self.status.uptime_seconds = (datetime.now() - start_dt).total_seconds()
                
                self.stop_event.wait(self.config_manager.check_interval)
            except Exception as e:
                self.logger.log(f"监控循环异常: {str(e)}", "ERROR")
                time.sleep(5)

    def _check_cycle(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        
        # 检查网站
        for name, cfg in self.config_manager.websites.items():
            if not cfg.enabled: continue
            try:
                import requests
                url = cfg.url if cfg.url else get_site_bindings_url(name)
                if not url: url = "http://localhost"
                
                resp = requests.get(url, timeout=cfg.check_timeout, verify=False, allow_redirects=True)
                is_ok = resp.status_code == cfg.expected_status
                
                with self.lock:
                    if name not in self.website_status: self.website_status[name] = {"status": "unknown", "fail_count": 0}
                    stat = self.website_status[name]
                    stat["last_check"] = current_time
                    stat["total_checks"] += 1
                    
                    if is_ok:
                        stat["status"] = "ok"
                        stat["fail_count"] = 0
                    else:
                        stat["status"] = "error"
                        stat["fail_count"] += 1
                        self._handle_failure(name, "网站", stat)
                        
            except Exception as e:
                with self.lock:
                    if name not in self.website_status: self.website_status[name] = {"status": "unknown", "fail_count": 0}
                    stat = self.website_status[name]
                    stat["status"] = "error"
                    stat["fail_count"] += 1
                    stat["last_check"] = current_time
                    self._handle_failure(name, "网站", stat)

        # 检查应用池
        for name, cfg in self.config_manager.app_pools.items():
            if not cfg.enabled: continue
            try:
                state = get_app_pool_state(name)
                is_ok = state == "Started"
                
                with self.lock:
                    if name not in self.apppool_status: self.apppool_status[name] = {"status": "unknown", "fail_count": 0}
                    stat = self.apppool_status[name]
                    stat["last_check"] = current_time
                    stat["total_checks"] += 1
                    
                    if is_ok:
                        stat["status"] = "running"
                        stat["fail_count"] = 0
                    else:
                        stat["status"] = "stopped"
                        stat["fail_count"] += 1
                        self._handle_failure(name, "应用池", stat)
                        
            except Exception as e:
                self.logger.log(f"检查应用池 {name} 异常: {e}", "ERROR")

    def _handle_failure(self, name, type_name, stat):
            self.status.total_failures += 1
        
            # 判断是否达到最大失败次数
            if stat["fail_count"] >= self.config_manager.max_failures:
                if self.config_manager.global_auto_restart:
                    self.logger.log(f"{type_name} {name} 连续失败 {stat['fail_count']} 次，准备重启...", "WARNING")
                    
                    restart_success = False
                    
                    if type_name == "网站":
                        # 只有当网站配置中启用了监控时才重启 (默认为True)
                        if name in self.config_manager.websites:
                            if restart_website(name):
                                self.status.total_restarts += 1
                                restart_success = True
                                self.logger.log(f"✅ 网站 {name} 重启成功", "INFO")
                            else:
                                self.logger.log(f"❌ 网站 {name} 重启失败", "ERROR")
                                
                    elif type_name == "应用池":
                        # 只有当应用池配置中启用了监控时才重启 (默认为True)
                        if name in self.config_manager.app_pools:
                            if restart_app_pool(name):
                                self.status.total_restarts += 1
                                restart_success = True
                                self.logger.log(f"✅ 应用池 {name} 重启成功", "INFO")
                            else:
                                self.logger.log(f"❌ 应用池 {name} 重启失败", "ERROR")
                    
                    # 只有重启成功才重置失败计数
                    if restart_success:
                        stat["fail_count"] = 0


    def get_status_snapshot(self):
        """
        获取状态快照 (用于UI更新)
        加上锁超时，防止UI卡死
        """
        try:
            acquired = self.lock.acquire(timeout=0.1) # 100ms超时
            if not acquired:
                return None # 如果获取锁失败，跳过本次更新
                
            try:
                return {
                    "monitor": asdict(self.status),
                    "websites": dict(self.website_status),
                    "pools": dict(self.apppool_status),
                    "config": {
                        "interval": self.config_manager.check_interval,
                        "max_fail": self.config_manager.max_failures
                    }
                }
            finally:
                self.lock.release()
        except Exception:
            return None

# ============================================
# Gradio 界面
# ============================================
monitor = IISMonitor()

def get_safe_json_dump(data):
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except:
        return "{}"
def refresh_sites_with_status():
        """刷新网站列表并附带状态"""
        try:
            sites = get_iis_sites()
            if not sites:
                return "未找到网站"
            
            status_lines = []
            for s in sites:
                name = s['name']
                state = s['state'] # Started 或 Stopped
                
                # 根据状态添加不同的图标
                icon = "🟢" if state == "Started" else "🔴"
                
                # 格式: 🟢 Default Web Site (运行中)
                status_lines.append(f"{icon} [{state}] - {name} (ID: {s['id']})")
                
            return "\n".join(status_lines)
            
        except Exception as e:
            return f"读取失败: {str(e)}"


def refresh_iis_info(type_):
    """安全的刷新函数"""
    try:
        if type_ == "site":
            sites = get_iis_sites()
            if sites:
                return "\n".join([f"- {s['name']} (ID: {s['id']})" for s in sites])
            return "未找到网站"
        else:
            pools = get_iis_app_pools()
            if pools:
                return "\n".join([f"{'🟢' if p['state'] == 'Started' else '🔴'} [{p['state']}] - {p['name']} " for p in pools])
            return "未找到应用池"
    except Exception as e:
        return f"读取失败: {str(e)}"

def update_ui():
    """核心UI更新函数，防止卡死"""
    snapshot = monitor.get_status_snapshot()
    
    if not snapshot:
        # 如果获取不到数据（锁被占用），返回当前值或占位符
        return gr.skip() 
    
    mon = snapshot["monitor"]
    
    status_text = "🟢 运行中" if mon["status"] == "running" else "🔴 已停止"
    runtime = format_duration(mon["uptime_seconds"])
    
    # 构建网站表格
    website_rows = []
    for name, conf in monitor.config_manager.websites.items():
        stat = snapshot["websites"].get(name, {})
        website_rows.append([
            name,
            conf.url,
            stat.get("status", "-"),
            stat.get("fail_count", 0),
            stat.get("last_check", "-")
        ])
        
    # 构建应用池表格
    pool_rows = []
    for name, conf in monitor.config_manager.app_pools.items():
        stat = snapshot["pools"].get(name, {})
        pool_rows.append([
            name,
            stat.get("status", "-"),
            stat.get("fail_count", 0),
            stat.get("last_check", "-")
        ])
        
    logs = "\n".join(monitor.logger.get_recent_logs(50))
    
    return (
        status_text,
        runtime,
        f"{mon['total_checks']} 次",
        f"{mon['total_failures']} 次",
        f"{mon['total_restarts']} 次",
        f"{snapshot['config']['interval']} 秒",
        f"{snapshot['config']['max_fail']} 次",
        website_rows,
        pool_rows,
        logs
    )

def initialize_page():
    """页面初始化，自动加载已保存的配置"""
    try:
        print(">>> 开始初始化页面...")
        
        # 1. 刷新IIS信息
        sites_txt = refresh_sites_with_status()
        pools_txt = refresh_iis_info("pool")
        
        # 2. 处理网站配置
        web_final = {}
        if monitor.config_manager.websites:
            for name, conf_obj in monitor.config_manager.websites.items():
                attrs = vars(conf_obj)
                # 补全字段
                attrs.setdefault("url", "")
                attrs.setdefault("enabled", True)
                attrs.setdefault("check_timeout", 10)
                attrs.setdefault("expected_status", 200)
                web_final[name] = attrs
        print(f">>> 已读取网站配置数量: {len(web_final)}")

        # 3. 处理应用池配置
        pool_final = {}
        if monitor.config_manager.app_pools:
            for name, conf_obj in monitor.config_manager.app_pools.items():
                attrs = vars(conf_obj)
                attrs.setdefault("enabled", True)
                attrs.setdefault("auto_restart", True)
                attrs.setdefault("restart_delay", 5)
                pool_final[name] = attrs
        print(f">>> 已读取应用池配置数量: {len(pool_final)}")

        # 4. 转换为 JSON 字符串
        # 如果没有配置，生成带提示的空 JSON，而不是 "{}"，方便用户编辑
        web_json = json.dumps(web_final, indent=2, ensure_ascii=False) if web_final else '{\n  \n}'
        pool_json = json.dumps(pool_final, indent=2, ensure_ascii=False) if pool_final else '{\n  \n}'
        
        # 5. 获取全局参数
        interval = monitor.config_manager.check_interval
        max_fail = monitor.config_manager.max_failures
        auto_restart = monitor.config_manager.global_auto_restart
        
        print(f">>> 全局参数: Interval={interval}, Fail={max_fail}, Restart={auto_restart}")
        
        # 6. 返回结果，顺序必须严格匹配 demo.load 的 outputs 列表
        return (
            web_json,       # 1. txt_web_conf
            pool_json,      # 2. txt_pool_conf
            interval,       # 3. sld_interval
            max_fail,       # 4. sld_fail
            auto_restart,   # 5. chk_restart
            sites_txt,      # 6. txt_avail_sites_status
            pools_txt       # 7. txt_avail_pools
        )
        
    except Exception as e:
        import traceback
        print(f"❌ 初始化严重错误: {e}")
        traceback.print_exc()
        return ("加载出错", "加载出错", 60, 3, True, "出错", "出错")




def save_web_config_json(json_str):
    try:
        # 清理JSON字符串，处理空值
        if not json_str or json_str.strip() == "":
            json_str = "{}"
            
        data = json.loads(json_str)
        monitor.config_manager.websites.clear()
        
        # 统计信息
        success_count = 0
        error_count = 0
        errors = []
        
        for site_name, site_data in data.items():
            try:
                # 如果JSON中的site_name为空，跳过
                if not site_name or site_name.strip() == "":
                    continue
                    
                # 确保site_data是字典类型
                if not isinstance(site_data, dict):
                    site_data = {}
                
                # 设置默认值
                config_data = {
                    "url": site_data.get("url", ""),
                    "enabled": bool(site_data.get("enabled", True)),
                    "check_timeout": int(site_data.get("check_timeout", 10)),
                    "expected_status": int(site_data.get("expected_status", 200))
                }
                
                # 创建配置对象
                website_config = WebsiteConfig(**config_data)
                
                # 添加到配置管理器
                monitor.config_manager.websites[site_name] = website_config
                success_count += 1
                
                print(f"✅ 成功添加网站配置: {site_name} - URL: {config_data['url']}")
                
            except Exception as e:
                error_count += 1
                error_msg = f"网站 '{site_name}' 配置错误: {str(e)}"
                errors.append(error_msg)
                print(f"❌ {error_msg}")
        
        # 保存到配置文件
        monitor.config_manager.save()
        
        # 重新初始化状态
        monitor._init_status()
        
        # 构建结果消息
        result_msg = f"✅ 网站配置已保存\n"
        result_msg += f"- 成功: {success_count} 个\n"
        result_msg += f"- 失败: {error_count} 个\n"
        
        if errors:
            result_msg += "\n错误详情:\n" + "\n".join(errors[:5])  # 只显示前5个错误
            if len(errors) > 5:
                result_msg += f"\n... 还有 {len(errors)-5} 个错误"
        
        return result_msg
        
    except json.JSONDecodeError as e:
        error_msg = f"❌ JSON格式错误: {str(e)}\n\n请确保输入的是有效的JSON格式"
        print(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"❌ 保存失败: {str(e)}"
        print(error_msg)
        return error_msg


def save_pool_config_json(json_str):
    try:
        # 清理JSON字符串
        if not json_str or json_str.strip() == "":
            json_str = "{}"
            
        data = json.loads(json_str)
        monitor.config_manager.app_pools.clear()
        
        # 统计信息
        success_count = 0
        error_count = 0
        errors = []
        
        for pool_name, pool_data in data.items():
            try:
                # 如果JSON中的pool_name为空，跳过
                if not pool_name or pool_name.strip() == "":
                    continue
                    
                # 确保pool_data是字典类型
                if not isinstance(pool_data, dict):
                    pool_data = {}
                
                # 设置默认值
                config_data = {
                    "enabled": bool(pool_data.get("enabled", True)),
                    "auto_restart": bool(pool_data.get("auto_restart", True)),
                    "restart_delay": int(pool_data.get("restart_delay", 5))
                }
                
                # 创建配置对象
                apppool_config = AppPoolConfig(**config_data)
                
                # 添加到配置管理器
                monitor.config_manager.app_pools[pool_name] = apppool_config
                success_count += 1
                
                print(f"✅ 成功添加应用池配置: {pool_name}")
                
            except Exception as e:
                error_count += 1
                error_msg = f"应用池 '{pool_name}' 配置错误: {str(e)}"
                errors.append(error_msg)
                print(f"❌ {error_msg}")
        
        # 保存到配置文件
        monitor.config_manager.save()
        
        # 重新初始化状态
        monitor._init_status()
        
        # 构建结果消息
        result_msg = f"✅ 应用池配置已保存\n"
        result_msg += f"- 成功: {success_count} 个\n"
        result_msg += f"- 失败: {error_count} 个\n"
        
        if errors:
            result_msg += "\n错误详情:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                result_msg += f"\n... 还有 {len(errors)-5} 个错误"
        
        return result_msg
        
    except json.JSONDecodeError as e:
        error_msg = f"❌ JSON格式错误: {str(e)}\n\n请确保输入的是有效的JSON格式"
        print(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"❌ 保存失败: {str(e)}"
        print(error_msg)
        return error_msg

# ============================================
# 界面构建
# ============================================

# 创建两个状态变量，用于在后台存储配置
web_conf_state = gr.State(value={})
pool_conf_state = gr.State(value={})

with gr.Blocks(title="IIS Monitor") as demo:
    gr.Markdown("# 🛠️ IIS 监控管理系统")
    
    with gr.Tabs():
        with gr.Tab("📊 监控面板"):
            with gr.Row():
                with gr.Column(scale=1):
                    status_box = gr.Textbox(label="当前状态", value="未启动", interactive=False)
                    with gr.Row():
                        btn_start = gr.Button("▶️ 启动", variant="primary")
                        btn_stop = gr.Button("⏹️ 停止", variant="stop")
                    
                    gr.Markdown("### 统计")
                    txt_runtime = gr.Textbox(label="运行时长", interactive=False)
                    txt_checks = gr.Textbox(label="总检测", interactive=False)
                    txt_fails = gr.Textbox(label="总失败", interactive=False)
                    txt_restarts = gr.Textbox(label="总重启", interactive=False)
                    txt_interval = gr.Textbox(label="检测间隔", interactive=False)
                    txt_maxfail = gr.Textbox(label="失败阈值", interactive=False)
                    
                with gr.Column(scale=2):
                    gr.Markdown("### 🌐 网站状态")
                    df_websites = gr.Dataframe(headers=["名称", "URL", "状态", "失败", "最后检测"], label="网站列表")
                    gr.Markdown("### 🏊 应用池状态")
                    df_pools = gr.Dataframe(headers=["名称", "状态", "失败", "最后检测"], label="应用池列表")
                    
            with gr.Accordion("📋 日志", open=False):
                txt_logs = gr.Textbox(lines=10, interactive=False)

        with gr.Tab("⚙️ 配置") as config_tab:
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### 1. 获取IIS信息")
                    btn_refresh_sites = gr.Button("🔄 刷新网站列表")
                    txt_avail_sites_status = gr.Textbox(label="可用网站", lines=8, interactive=False,placeholder="点击刷新按钮获取...")
                    
                    gr.Markdown("### 2. 网站监控配置")
                    txt_web_conf = gr.Textbox(label="JSON配置", lines=8, placeholder="""{
	"Example Web Site": {
		"url": "http://localhost:80",
		"enabled": true,
		"check_timeout": 10,
		"expected_status": 200
	},
	"ExampleWebsite": {
		"url": "http://localhost:8080",
		"enabled": true,
		"check_timeout": 15,
		"expected_status": 200
	}
}""")
                    btn_save_web = gr.Button("💾 保存网站配置")
                    txt_web_status = gr.Textbox(label="结果", interactive=False)
                    
                with gr.Column():
                    gr.Markdown("### 1. 获取IIS应用池")
                    btn_refresh_pools = gr.Button("🔄 刷新应用池列表")
                    txt_avail_pools = gr.Textbox(label="可用应用池", lines=8, interactive=False,placeholder="点击刷新按钮获取...")
                    
                    gr.Markdown("### 2. 应用池监控配置")
                    txt_pool_conf = gr.Textbox(label="JSON配置", lines=8, placeholder="""{
	"ExampleAppPool": {
		"enabled": true,
		"auto_restart": true,
		"restart_delay": 5
	},
	"MyExampleAppPool": {
		"enabled": true,
		"auto_restart": true,
		"restart_delay": 10
	}
}""")
                    btn_save_pool = gr.Button("💾 保存应用池配置")
                    txt_pool_status = gr.Textbox(label="结果", interactive=False)
            
            gr.Markdown("### 3. 全局参数")
            with gr.Row():
                sld_interval = gr.Slider(10, 300, value=60, label="检测间隔(秒)")
                sld_fail = gr.Slider(1, 10, value=3, label="最大失败次数")
                chk_restart = gr.Checkbox(value=True, label="允许自动重启")
            btn_save_global = gr.Button("💾 保存全局设置")
            txt_global_status = gr.Textbox(label="结果", interactive=False)

    # ============================================
    # 事件绑定
    # ============================================
    
    # 按钮事件
    btn_start.click(lambda: monitor.start(), outputs=[status_box])
    btn_stop.click(lambda: monitor.stop(), outputs=[status_box])
    
    # 刷新列表
#    btn_refresh_sites.click(refresh_iis_info, inputs=[gr.State("site")], outputs=[txt_avail_sites])
    btn_refresh_sites.click(refresh_sites_with_status, outputs=[txt_avail_sites_status])
    btn_refresh_pools.click(refresh_iis_info, inputs=[gr.State("pool")], outputs=[txt_avail_pools])
	
	# 保存配置
    btn_save_web.click(save_web_config_json, inputs=[txt_web_conf], outputs=[txt_web_status])
    btn_save_pool.click(save_pool_config_json, inputs=[txt_pool_conf], outputs=[txt_pool_status])
    
    def save_global(interval, fail, restart):
        try:
            monitor.config_manager.check_interval = int(interval)
            monitor.config_manager.max_failures = int(fail)
            monitor.config_manager.global_auto_restart = restart
            monitor.config_manager.save()
            return "✅ 全局配置已保存"
        except Exception as e:
            return f"❌ 失败: {e}"
            
    btn_save_global.click(save_global, inputs=[sld_interval, sld_fail, chk_restart], outputs=[txt_global_status])
    
    # 定时器 (每1秒更新一次UI，使用了skip逻辑优化性能)
    timer = gr.Timer(1.0)
    timer.tick(
        update_ui,
        outputs=[
            status_box, txt_runtime, txt_checks, txt_fails, txt_restarts, 
            txt_interval, txt_maxfail, df_websites, df_pools, txt_logs
        ]
    )
    # 最简单的解决方案 - 在页面加载时就完成所有初始化
    def initialize_all_on_load():
        """页面加载时一次性初始化所有内容"""
        try:
            # 1. 获取IIS信息
            sites_txt = refresh_sites_with_status()
            pools_txt = refresh_iis_info("pool")
        
            # 2. 加载网站配置
            web_final = {}
            for name, conf_obj in monitor.config_manager.websites.items():
                attrs = vars(conf_obj)
                attrs.setdefault("url", "")
                attrs.setdefault("enabled", True)
                attrs.setdefault("check_timeout", 10)
                attrs.setdefault("expected_status", 200)
                web_final[name] = attrs
        
            # 3. 加载应用池配置
            pool_final = {}
            for name, conf_obj in monitor.config_manager.app_pools.items():
                attrs = vars(conf_obj)
                attrs.setdefault("enabled", True)
                attrs.setdefault("auto_restart", True)
                attrs.setdefault("restart_delay", 5)
                pool_final[name] = attrs
        
            # 4. 转换为JSON
            web_json = json.dumps(web_final, indent=2, ensure_ascii=False) if web_final else '{\n  \n}'
            pool_json = json.dumps(pool_final, indent=2, ensure_ascii=False) if pool_final else '{\n  \n}'
        
            # 5. 全局设置
            interval = monitor.config_manager.check_interval
            max_fail = monitor.config_manager.max_failures
            auto_restart = monitor.config_manager.global_auto_restart
        
            print(f">>> 初始化完成: {len(web_final)}个网站, {len(pool_final)}个应用池")

            # 如果没有配置，提供友好的示例
            if len(web_final)==0:
                # web_final = {
                #     "Example Web Site": {
                #         "url": "http://localhost:80",
                #         "enabled": true,
                #         "check_timeout": 10,
                #         "expected_status": 200
                #     },
                #     "ExampleWebsite": {
                #         "url": "http://localhost:8080",
                #         "enabled": true,
                #         "check_timeout": 15,
                #         "expected_status": 200
                #     }
                # }
                # web_json = json.dumps(web_final, indent=2, ensure_ascii=False)
                web_json=None
                print(">>> 网站配置为空，显示示例配置")
            
            
            # 如果没有配置，提供友好的示例
            if len(pool_final)==0:
                # pool_final = {
                #     "ExampleAppPool": {
                #         "enabled": true,
                #         "auto_restart": true,
                #         "restart_delay": 5
                #     },
                #     "MyExampleAppPool": {
                #         "enabled": true,
                #         "auto_restart": true,
                #         "restart_delay": 10
                #     }
                # }
                # pool_json = json.dumps(pool_final, indent=2, ensure_ascii=False)
                pool_json=None
                print(">>> 应用池配置为空，显示示例配置")
            
            return (
                web_json, pool_json, interval, max_fail, auto_restart, 
                sites_txt, pools_txt, interval, max_fail, "未启动"
            )
        
        except Exception as e:
            print(f"初始化错误: {e}")
            return ("{}", "{}", 60, 3, True, "初始化失败", "初始化失败", 60, 3, "未启动")

    # 一次性初始化所有
    demo.load(
        fn=initialize_all_on_load,
        outputs=[
            txt_web_conf, txt_pool_conf, sld_interval, sld_fail, chk_restart,
            txt_avail_sites_status , txt_avail_pools, txt_interval, txt_maxfail, txt_runtime
        ]
    )
    
    # 配置Tab点击时只刷新IIS列表（可选，如果觉得每次点击都刷新太频繁可以去掉）
    def refresh_lists_on_tab_click():
        """点击配置Tab时刷新列表"""
        sites = refresh_sites_with_status()
        pools = refresh_iis_info("pool")
        return sites, pools

    config_tab.select(
        fn=refresh_lists_on_tab_click,
        outputs=[txt_avail_sites_status, txt_avail_pools]
    )
    
if __name__ == "__main__":
    print("-" * 30)
    print("IIS监控启动中...")
    print(f"IIS监控已启动！本机可在浏览器中访问: http://localhost:7860")
    demo.launch(server_name="0.0.0.0", server_port=7860,theme=gr.themes.Soft(),css=custom_css)
