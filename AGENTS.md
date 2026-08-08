# PushToTelegram 项目专属规则

通用规则见：`E:\@imFile-Download\AI-Useful-Prompt\通用开发工作规则.md`。

- 本项目是个人使用的 Bilibili UP 投稿提醒工具，包含 Python 业务代码、桌面界面和 PyInstaller 配置；主入口包括 `app.py`、`backend_api.py` 和 `telegram_client.py`。
- 开发验证优先使用项目现有 Python 环境；正式打包授权后使用 `npm run build:pyqt`，不要擅自改为其他打包方式。
- 项目名与交付映射：`PushToTelegram -> PushToTelegram.exe`。
- 最终 Windows 产物只复制到 `D:\@Software\PushToTelegram\PushToTelegram.exe`，不要复制 `build`、`dist`、PDB 或其他中间产物。
- `config.json`、`history.db`、`app.log`、本地 Telegram/Bilibili 凭据、Cookie、下载记录和去重记录属于个人运行数据，不要提交或覆盖。
- 涉及请求频率、随机间隔、投稿去重或 Telegram 推送时，保持现有的低频和防重复行为。
