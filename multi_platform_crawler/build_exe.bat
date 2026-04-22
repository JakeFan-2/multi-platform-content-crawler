@echo off
REM Build script for multi-platform crawler (PyInstaller)
set PYINSTALLER=.venv\Scripts\pyinstaller.exe
if not exist "%PYINSTALLER%" (
    echo PyInstaller not found in virtual env, trying global...
    set PYINSTALLER=pyinstaller
)

"%PYINSTALLER%" --onefile --windowed ^
    --name "MultiPlatformCrawler" ^
    --add-data "platforms;platforms" ^
    --add-data "config;config" ^
    --add-data ".env.example;.env.example" ^
    --hidden-import=playwright.async_api ^
    --hidden-import=asyncio ^
    --hidden-import=multiprocessing ^
    --hidden-import=dotenv ^
    --hidden-import=aiohttp ^
    --hidden-import=aiohttp.client ^
    --hidden-import=aiohttp.connector ^
    --hidden-import=aiohttp.http ^
    --hidden-import=aiohttp.http_exceptions ^
    --hidden-import=aiosignal ^
    --hidden-import=frozenlist ^
    --hidden-import=propcache ^
    --hidden-import=attr ^
    --hidden-import=yarl ^
    --hidden-import=multidict ^
    --hidden-import=async_timeout ^
    --hidden-import=utils.feishu_exporter ^
    --hidden-import=utils.feishu_worker ^
    --hidden-import=tenacity ^
    --collect-all=aiohttp ^
    --hidden-import=utils.title_matcher ^
    --hidden-import=utils.data_model ^
    --hidden-import=utils.snapshot ^
    --hidden-import=utils.security ^
    --hidden-import=utils.ops ^
    --hidden-import=utils.exposure_loader ^
    --hidden-import=utils.platform_registry ^
    --hidden-import=utils.path_helper ^
    --hidden-import=utils.env_loader ^
    --hidden-import=utils.module_loader ^
    --hidden-import=utils.playwright_cleanup ^
    --collect-submodules utils ^
    --collect-all=utils ^
    --collect-all=playwright ^
    --collect-all=playwright_stealth ^
    main.py

echo Build finished. Exe located in dist\MultiPlatformCrawler.exe
pause