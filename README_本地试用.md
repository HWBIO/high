# HH Match Agent Windows 本地试用说明

这个试用包用于让用户先在自己的 Windows 电脑上体验 Agent，不需要部署服务器，也不需要运行 `npm run dev`。

## 使用步骤

1. 解压试用包。
2. 把发送人提供的 `agent-config.env` 放到解压后的最外层文件夹，也就是和 `start-windows.bat` 放在一起。
3. 双击 `start-windows.bat`。
4. 浏览器打开 `http://127.0.0.1:8720/match`。
5. 注册或登录 Agent 账号。
6. 开始导入客户、岗位、简历，测试匹配流程。

最外层文件夹放好后应该大概是这样：

```text
hh-match-agent-windows-trial
├─ start-windows.bat
├─ agent-config.env
├─ README_本地试用.md
├─ backend
└─ frontend
```

## 需要提前准备

- Windows 10/11。
- Python 3.10 或以上版本。
- `agent-config.env` 配置文件。
- 电脑能访问模型接口和飞书接口。

正常试用不需要安装 Node.js。试用包里已经包含 `frontend/dist`，后端会直接托管前端页面。

只有当 `frontend/dist` 缺失时，启动脚本才会检查 Node.js / npm，并尝试自动构建前端。

## 配置文件怎么处理

发送人会单独给你一个配置文件，文件名一般是：

```text
agent-config.env
```

你只需要把它放到解压后的最外层文件夹，和 `start-windows.bat` 放在一起。

启动脚本会自动把它复制成：

```text
backend/.env
```

你不需要打开或编辑这个文件。

如果发送人给你的文件名是 `试用配置.env`，也可以直接放在最外层文件夹，启动脚本同样会识别。

配置文件里至少包括：

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
OPENAI_MODEL=...

FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_BASE_TOKEN=...
FEISHU_TABLE_COMPANIES=...
FEISHU_TABLE_USERS=...
FEISHU_TABLE_PROJECTS=...
FEISHU_TABLE_POSITIONS=...
FEISHU_TABLE_PROMPTS=...
FEISHU_TABLE_MATCH_RESULTS=...
FEISHU_TABLE_CLIENT_RECOMMENDATIONS=...
FEISHU_TABLE_CHAT_MESSAGES=...
FEISHU_TABLE_TRACKING=...
FEISHU_SYNC_ENABLED=true
FEISHU_SYNC_STRICT=true
```

本地试用时，启动脚本会临时把页面地址设置为：

```env
APP_PUBLIC_URL=http://127.0.0.1:8720
CORS_ALLOW_ORIGINS=http://127.0.0.1:8720
```

所以试用者打开 `http://127.0.0.1:8720/match` 即可。

如果双击后提示“没有找到配置文件”，说明 `agent-config.env` 没有放对位置。请确认它和 `start-windows.bat` 在同一个文件夹里。

## 数据规则

本地试用版的数据分两层：

- 页面读取和账号登录使用本机 SQLite：`backend/data/hh.db`。
- 客户、岗位、提示词等关键数据会同步写入同一个飞书 Base。

这意味着本地试用适合验证功能流程，不适合多人协作共用同一套页面数据。正式多人共用需要部署到一台服务器，大家访问同一个 URL。

## 常见问题

### 1. 双击后提示找不到 Python

安装 Python 3.10 或以上版本，并勾选 `Add python.exe to PATH`。

下载地址：

```text
https://www.python.org/downloads/windows/
```

安装后重新双击 `start-windows.bat`。

### 2. 第一次启动很慢

第一次启动会创建 `backend/.venv` 并安装 Python 依赖，可能需要几分钟。后续启动会快很多。

### 3. 提示 pip 安装失败

通常是网络无法访问 Python 包源。可以换网络，或让发送人提供带依赖的离线包。

### 4. 页面能打开，但新增客户/岗位报飞书错误

正式同步模式是 `FEISHU_SYNC_STRICT=true`。如果飞书配置、权限或字段有问题，系统会阻断写入，避免页面显示成功但飞书没有保存。

请检查：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_TOKEN`
- 各 `FEISHU_TABLE_*`
- 飞书应用是否有 Base 权限
- 飞书 Base 是否授权给该应用

### 5. 模型调用失败

请检查：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- 当前电脑是否能访问模型网关

## 停止服务

回到启动窗口，按 `Ctrl + C`，然后输入 `Y` 确认。

## 正式部署区别

本地试用版是每个人在自己电脑上跑一个进程。

正式部署版只部署一套共享服务，团队用户访问同一个网站，例如：

```text
https://agent.innoreal.cn/match
```

正式部署需要服务器、域名解析、HTTPS 证书和反向代理。
