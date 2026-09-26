"""【存量代码 · 示例】GitHub 提交采集。

⚠️ 本文件是刻意构造的"存量系统"样本，用于演示《SDD 实战》第 7.4 节
   "为存量系统补写规范"的完整流程。

它具备书中 7.4.1 描述的存量系统全部四个特征：
  - 逻辑成谜：为什么重试 3 次？为什么超时 30s？没有任何注释或文档说明
  - 需求散佚：不存在 proposal.md，需求只存在于代码与口头约定中
  - 架构漂移：本该返回记录列表的函数，顺手把日志格式化也做了
  - 测试薄弱：没有任何测试

不要在真实项目里模仿这里的写法 —— 它的价值恰恰是"反面样本"。
"""

import json
import time
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"


class GithubCollector:
    def __init__(self, token, repos):
        self.token = token
        self.repos = repos
        self.session_ok = True

    def collect(self, since, until):
        out = []
        for r in self.repos:
            page = 1
            while True:
                url = (
                    GITHUB_API
                    + "/repos/"
                    + r
                    + "/commits?since="
                    + since
                    + "&until="
                    + until
                    + "&per_page=30&page="
                    + str(page)
                )
                req = urllib.request.Request(url)
                req.add_header("Authorization", "Bearer " + self.token)
                req.add_header("Accept", "application/vnd.github+json")
                tries = 0
                resp = None
                while tries < 3:
                    try:
                        resp = urllib.request.urlopen(req, timeout=30)
                        break
                    except Exception:
                        tries = tries + 1
                        time.sleep(5)
                if resp is None:
                    self.session_ok = False
                    break
                code = resp.getcode()
                if code == 403:
                    # 限流了，等一会儿
                    time.sleep(60)
                    continue
                body = json.loads(resp.read().decode("utf-8"))
                if not body:
                    break
                for it in body:
                    c = it["commit"]
                    d = {
                        "who": c["author"]["name"],
                        "msg": c["message"],
                        "when": c["author"]["date"],
                        "repo": r,
                        "add": 0,
                        "del": 0,
                        "files": 0,
                    }
                    detail_url = GITHUB_API + "/repos/" + r + "/commits/" + it["sha"]
                    dreq = urllib.request.Request(detail_url)
                    dreq.add_header("Authorization", "Bearer " + self.token)
                    try:
                        dresp = urllib.request.urlopen(dreq, timeout=30)
                        dbody = json.loads(dresp.read().decode("utf-8"))
                        d["add"] = dbody.get("stats", {}).get("additions", 0)
                        d["del"] = dbody.get("stats", {}).get("deletions", 0)
                        d["files"] = len(dbody.get("files", []))
                    except Exception:
                        pass
                    out.append(d)
                page = page + 1

        # 顺手把结果拼成一段文本，方便直接打印（职责越界）
        lines = []
        for d in out:
            lines.append(
                "%s 在 %s 提交了 %s（+%d/-%d，%d 个文件）"
                % (d["who"], d["repo"], d["msg"], d["add"], d["del"], d["files"])
            )
        if not lines:
            return ""
        return "\n".join(lines)

    def check_token(self):
        req = urllib.request.Request(GITHUB_API + "/user")
        req.add_header("Authorization", "Bearer " + self.token)
        try:
            urllib.request.urlopen(req, timeout=30)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False
            return True
