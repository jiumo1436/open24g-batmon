# assets —— 截图与演示素材

本目录存放 README 与 Release 说明里引用的界面截图、托盘图标渲染图。

| 文件 | 内容 |
| --- | --- |
| `screenshot-main.png` | 主界面（设备卡片列表） |
| `screenshot-import.png` | 导入设备对话框（列出 `configs/` 下的配置） |
| `screenshot-settings.png` | 设置对话框（电量配色 / 托盘 / 开机自启 / 低电量提醒 / 串口） |
| `tray-icon.png` | 托盘三环图标的实际渲染效果 |

## 约定

- 截图统一 **1x 或 2x 整数倍**，不要拉伸；窗口边距裁掉，只留界面本体；
- 截图中**不得出现**真实设备序列号、用户名、本地路径、MAC 地址；
- 截图里的设备名与电量来自示例配置与界面示意，不代表任何真实产品；
- 新增截图请用小写英文 + 连字符命名，并在本文件表格里登记；
- 不要提交 `*.pcap`、录屏原始文件到本目录（体积大且可能含隐私），需要演示时上传到
  Release 或 issue 附件。

## 重新生成截图

在 Windows 上启动程序，用系统截图工具（`Win + Shift + S`）截取窗口即可。
若需在无人值守环境生成，可参考下面的做法（离屏渲染，不需要真实显示器）：

```python
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import app
from PySide6.QtWidgets import QApplication

qa = QApplication([])
qa.setStyleSheet(app.STYLE)
win = app.MainWindow()
win.show()
qa.processEvents()
win.grab().save("assets/screenshot-main.png")
```

> 离屏渲染得到的界面与实际显示一致，但字体渲染可能因系统缺少字体而略有差异，
> 正式截图仍建议在真实桌面上截取。
