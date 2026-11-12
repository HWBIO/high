@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8720"
set "APP_PUBLIC_URL=http://127.0.0.1:%PORT%"
set "CORS_ALLOW_ORIGINS=http://127.0.0.1:%PORT%"

echo.
echo ==============================================
echo   HH Match Agent Windows 本地试用启动器
echo ==============================================
echo.

if not exist "backend\app\main.py" (
  echo [错误] 没找到 backend\app\main.py。
  echo 请确认你是在解压后的项目根目录里双击 start-windows.bat。
  pause
  exit /b 1
)

if not exist "frontend\dist\index.html" (
  echo [提示] 没找到 frontend\dist\index.html，需要先构建前端。
  echo [检查] 正在检查 Node.js / npm...
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [错误] 未检测到 Node.js / npm。
    echo 本试用包正常情况下应已包含 frontend\dist，不需要你运行 npm。
    echo 如果缺少 dist，请安装 Node.js 18+ 后重新运行，或向发送人索要完整试用包。
    pause
    exit /b 1
  )
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    echo [错误] npm install 失败。
    pause
    exit /b 1
  )
  call npm run build
  if errorlevel 1 (
    popd
    echo [错误] npm run build 失败。
    pause
    exit /b 1
  )
  popd
) else (
  echo [OK] 已找到前端构建产物 frontend\dist，将由后端直接托管。
  echo [提示] 不需要运行 npm run dev。
)

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 --version >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [错误] 未检测到 Python。
  echo 请先安装 Python 3.10 或以上版本，并勾选 "Add python.exe to PATH"。
  echo 下载地址：https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

echo [OK] Python 已找到。

if not exist "backend\.env" (
  if exist "agent-config.env" (
    echo [配置] 已找到 agent-config.env，正在复制到 backend\.env ...
    copy "agent-config.env" "backend\.env" >nul
  ) else if exist "试用配置.env" (
    echo [配置] 已找到 试用配置.env，正在复制到 backend\.env ...
    copy "试用配置.env" "backend\.env" >nul
  ) else (
    echo [提示] 没有找到配置文件。
    echo.
    echo 请把发送人提供的 agent-config.env 放到当前文件夹，也就是和 start-windows.bat 同一层。
    echo 然后重新双击 start-windows.bat。
    echo.
    echo 如果你只有 backend\.env.example，说明还没有拿到可用配置，不能直接试用模型和飞书同步。
    pause
    exit /b 1
  )
)

if not exist "backend\.venv\Scripts\python.exe" (
  echo [首次启动] 正在创建 Python 虚拟环境 backend\.venv ...
  %PYTHON_CMD% -m venv backend\.venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
)

echo [依赖] 正在安装/检查后端依赖，首次运行可能需要几分钟...
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo [错误] 后端依赖安装失败。
  echo 请确认当前电脑可以访问 Python pip 源，或联系发送人提供离线依赖包。
  pause
  exit /b 1
)

echo.
echo ==============================================
echo   启动成功后请打开：
echo   http://127.0.0.1:%PORT%/match
echo.
echo   如果浏览器没有自动打开，请手动复制上面的地址。
echo   停止服务：回到本窗口按 Ctrl + C。
echo ==============================================
echo.

start "" "http://127.0.0.1:%PORT%/match"

pushd backend
".venv\Scripts\python.exe" -m uvicorn app.main:app --host %HOST% --port %PORT%
popd

pause
