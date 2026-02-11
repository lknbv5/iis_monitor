# 🛠️ IIS 监控管理系统 (Gradio版)

一个基于 Python 和 Gradio 开发的轻量级 IIS 网站监控与管理系统。通过直观的 Web 界面，实时监控网站状态，并提供一键重启、日志查看等运维功能。

## ✨ 功能特性

* 📊 **实时仪表盘**：可视化的网站和应用池状态监控（运行中/已停止/异常）。
* 🔄 **一键运维**：支持直接在网页上对 IIS 网站和应用池进行 **启动**、**停止** 和 **回收** 操作。
* 📝 **实时日志**：集成实时日志输出窗口，方便追踪操作记录和错误信息。
* ⚙️ **灵活配置**：支持通过 JSON 配置文件自定义需要监控的网站和 URL。
* 🎨 **美观界面**：使用 Gradio 构建，界面现代、简洁，支持响应式布局（适配手机访问）。
* 🌐 **局域网访问**：支持局域网内其他设备访问，方便移动端远程管理。

## 📋 环境要求

* **操作系统**：Windows Server 2016+ 或 Windows 10/11
* **Python**：3.8 或更高版本
* **IIS**：已安装 IIS 服务

## 🚀 快速开始

### 1. 克隆或下载项目

将项目代码下载到本地服务器。

### 2. 安装 Python 依赖

在项目根目录下打开命令行（CMD/PowerShell），执行以下命令安装所需库：

```bash
pip install gradio requests
```

### 3. 开启 Windows 必要功能 (关键步骤)

本脚本依赖 IIS 的命令行管理工具 (`appcmd.exe`)。Windows 默认可能未安装此组件。

#### 方法 A：使用 PowerShell 命令 (推荐)

以**管理员身份**打开 PowerShell，复制并运行以下命令：

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName IIS-ManagementScriptingTools
Enable-WindowsOptionalFeature -Online -FeatureName IIS-ManagementService
```

> **注意**：
> 
> * `IIS-ManagementScriptingTools`：**必须安装**。它提供了 `appcmd.exe`，脚本的核心依赖。
> * `IIS-ManagementService`：推荐安装。用于远程管理支持。

#### 方法 B：图形界面开启

1. 按下 `Win + R`，输入 `optionalfeatures` 打开 **"启用或关闭 Windows 功能"**。
2. 找到并展开 **Internet Information Services**。
3. 展开 **万维网服务**。
4. 勾选 **IIS 6 管理兼容性** (确保子项 `IIS 管理控制台` 和 `IIS 管理脚本和工具` 也被勾选)。
5. 点击确定，等待安装完成。

### 4. 运行程序

**必须以管理员身份运行**脚本，否则无法对 IIS 进行启停操作。

1. 在开始菜单搜索 `cmd` 或 `python`。
2. 右键点击，选择 **"以管理员身份运行"**。
3. 切换到项目目录，运行：
   
   ```bash
   python iis_monitor.py
   ```

### 5. 访问界面

看到控制台输出 `Running on local URL...` 后，在浏览器中访问：

* **本机访问**：`http://localhost:7860`
* **局域网访问**：控制台会显示你的本机 IP（如 `http://192.168.x.x:7860`），使用该地址可在手机或同事电脑上访问。

---

## 📖 使用指南

### 仪表盘

* 显示所有配置的网站及其当前状态。
* **绿色**：正常运行；**红色**：停止或无法访问。
* 提供按钮对单个网站或应用池进行 **停止**、**启动**、**回收**。

### 配置页面

1. 点击 **"🔄 刷新网站列表"** 按钮。系统会列出当前 IIS 中所有的网站及其状态（例如：🟢 Default Web Site - [运行中]）。
2. 根据列表中的名称，编辑下方的 JSON 配置。
3. JSON 配置格式示例：
   
   ```json
   {
   "Example Web Site": {
       "url": "http://localhost:80",
       "enabled": True,
       "check_timeout": 10,
       "expected_status": 200
   },
   "ExampleWebsite": {
       "url": "http://localhost:8080",
       "enabled": True,
       "check_timeout": 15,
       "expected_status": 200
   }
   }
   ```
   
   ```json
   {
   "ExampleAppPool": {
       "enabled": True,
       "auto_restart": True,
       "restart_delay": 5
   },
   "MyExampleAppPool": {
       "enabled": True,
       "auto_restart": True,
       "restart_delay": 10
   }
   }
   ```
4. 点击 **"💾 保存网站配置"** 生效。

---

## ❓ 常见问题 (FAQ)

### 1. 运行报错：`'appcmd' 不是内部或外部命令`

**原因**：系统未安装 IIS 管理脚本工具。
**解决**：请按照上方 **步骤 3** 执行 PowerShell 命令或开启 Windows 功能。

### 2. 运行报错：`General access denied error` (访问被拒绝)

**原因**：权限不足。
**解决**：请务必 **右键键点击 CMD/PowerShell -> "以管理员身份运行"** 后再启动脚本。

### 3. 浏览器访问 `http://0.0.0.0:7860` 无法打开

**原因**：`0.0.0.0` 只是监听地址，不能直接在浏览器输入。
**解决**：

* 本机请访问：`http://localhost:7860`
* 局域网请访问：`http://[你的局域网IP]:7860` (IP 地址在启动程序时控制台会打印出来)

### 4. 页面字体显示奇怪

**原因**：未指定中文字体。
**解决**：确保代码中包含了自定义 CSS (`custom_css`)。若仍有问题，请尝试更换浏览器（推荐 Chrome 或 Edge）。

---

## 📦 依赖项

* `gradio`: Web UI 框架
* `requests`: HTTP 请求库，用于检测网站状态

---

## 📝 许可证

MIT License

---

**注意**：本工具仅用于服务器运维管理，请妥善保管管理员权限，避免在公网环境直接暴露管理端口。


