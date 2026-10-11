"""Windows桌面通知。"""

import base64
import html
import os
import subprocess


class WindowsNotifier:
    """通过Windows原生Toast发送桌面通知。"""

    @staticmethod
    def notify(title: str, message: str) -> None:
        title = title.strip()[:120]
        message = message.strip()[:500]
        xml = (
            "<toast>"
            "<visual><binding template=\"ToastGeneric\">"
            f"<text>{html.escape(title)}</text>"
            f"<text>{html.escape(message)}</text>"
            "</binding></visual>"
            "<audio src=\"ms-winsoundevent:Notification.Default\"/>"
            "</toast>"
        )
        encoded = base64.b64encode(xml.encode("utf-8")).decode("ascii")
        script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xmlText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell')
$notifier.Show($toast)
"""

        creation_flags = 0x08000000 if os.name == "nt" else 0
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=creation_flags,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "未知错误").strip()
            raise RuntimeError(f"Windows桌面通知发送失败: {error}")
