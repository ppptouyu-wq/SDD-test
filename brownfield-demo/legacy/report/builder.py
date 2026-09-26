"""【存量代码 · 示例】日报文本生成。

⚠️ 存量系统样本，用于演示第 7.4 节。典型问题：
  - 魔法数字：为什么是 60 字？为什么截断到 200 行？
  - 职责越界：生成函数里直接发邮件（应该由 notifier 负责）
  - 隐式排序：按作者名排序这件事只在代码里，没有任何文档
  - 无测试
"""

import smtplib
from email.mime.text import MIMEText


class ReportBuilder:
    def __init__(self, smtp_host, smtp_user, smtp_pass):
        self.smtp_host = smtp_host
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass

    def build(self, records, team):
        # 按作者分组
        g = {}
        for r in records:
            if r["who"] not in g:
                g[r["who"]] = []
            g[r["who"]].append(r)

        out = "【" + team + "】今日工作\n"
        for who in sorted(g.keys()):
            out = out + "\n== " + who + " ==\n"
            n = 0
            for r in g[who]:
                m = r["msg"]
                if len(m) > 60:
                    m = m[:60] + "..."
                out = (
                    out
                    + "- "
                    + m
                    + " (+"
                    + str(r["add"])
                    + "/-"
                    + str(r["del"])
                    + ")\n"
                )
                n = n + 1
                if n >= 200:
                    break
        return out

    def send(self, text, to):
        # 生成和发送混在一个类里
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = "日报"
        msg["From"] = self.smtp_user
        msg["To"] = to
        s = smtplib.SMTP_SSL(self.smtp_host, 465)
        s.login(self.smtp_user, self.smtp_pass)
        s.send_message(msg)
        s.quit()
        return True
