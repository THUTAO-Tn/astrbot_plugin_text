# astrbot_plugin_link_downloader · 链接下载器

AstrBot Star 插件：在**群聊**中发送 `/download 链接 [密码:xxxx]`，
插件自动识别链接类型、解析并检索网盘 / GitHub 内的文件清单，
群成员（**仅发起人**）回复序号即可把文件下载到本地临时目录并上传回当前群聊，
上传结束后自动删除临时文件。

- **仅群聊生效**：私聊消息一律拒绝，并提示「该指令仅限群聊使用」。
- **四类链接**：蓝奏云、百度网盘、夸克网盘、GitHub Release / Raw 直链。
- **镜像轮换**：GitHub 原始地址超时自动按配置的镜像列表循环重试，直到全部失败。
- **会话超时**：清单发出后等待选择，超时自动清空任务并提示过期。
- **体积保护**：单文件最大体积可配置，超限直接跳过并提示；上传前还会按适配器限制给出预警。

---

## 目录结构

```text
astrbot_plugin_link_downloader/
├── main.py                 # 插件入口：指令、会话管理、选择监听、下载与上传编排
├── metadata.yaml           # 插件元数据（AstrBot 依赖此文件识别插件）
├── _conf_schema.json       # 可视化配置 Schema（WebUI 配置面板）
├── requirements.txt        # 依赖（aiohttp）
├── README.md               # 本文档
└── parsers/                # 解析器模块（按站点拆分）
    ├── __init__.py         # 导出解析器 + build_parser 工厂
    ├── base.py             # 解析器抽象基类
    ├── http.py             # aiohttp 异步 HTTP 层（限速 / 流式下载 / 体积保护）
    ├── errors.py           # 业务异常（密码错误、解析失败、超时、超限…）
    ├── models.py           # RemoteFile 数据模型
    ├── utils.py            # 体积格式化、文件名清洗等工具
    ├── lanzou.py           # 蓝奏云解析
    ├── baidu.py            # 百度网盘解析
    ├── quark.py            # 夸克网盘解析
    └── github.py           # GitHub Release / Raw 解析 + 镜像轮换下载
```

> 插件只使用 `aiohttp`（AstrBot 本体已自带），`requirements.txt` 仅作依赖声明，
> 不会引入 requests 之类的阻塞式网络库。

---

## 安装步骤

### 方式一：WebUI 安装（推荐）

1. 打开 AstrBot WebUI → 左侧「插件」→「安装插件」；
2. 填写仓库地址 `https://github.com/<你的用户名>/astrbot_plugin_link_downloader`；
3. 安装完成后点击插件卡片上的「重载插件」，再到「插件配置」里按需调整参数。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/<你的用户名>/astrbot_plugin_link_downloader
```

然后在 WebUI「插件」页点击「重载插件」（或重启 AstrBot）。

### 目录示意

```text
AstrBot/
└── data/
    ├── plugins/
    │   └── astrbot_plugin_link_downloader/   # 本插件
    └── plugin_data/
        └── astrbot_plugin_link_downloader/
            └── tmp/                          # 下载临时目录（上传后自动清理）
```

### QQ（aiocqhttp）额外说明

- 插件默认开启 `use_aiocqhttp_upload`：在 QQ 平台会优先调用协议端的
  `upload_group_file` 接口直接上传群文件，失败时自动退回通用 `File` 消息段。
- 若协议端与 AstrBot 不在同一台机器，`upload_group_file` 可能找不到本地文件，
  此时代码会自动回退到通用文件消息。
- QQ 群文件有大小限制（普通用户一般 100MB 左右），请把 `max_file_size_mb`
  调整到与平台限制匹配的数值。

---

## 指令与示例

| 指令 | 说明 |
| --- | --- |
| `/download 链接` | 解析链接并列出文件清单 |
| `/download 链接 密码:xxxx` | 带提取码的分享 |
| `/dl 链接` | 指令别名，等价于 `/download` |

### 示例

```text
/download https://thutao.lanzoub.com/iOmYf4b2x8yd 密码:en15
/download https://pan.baidu.com/s/xxxxxx 密码:1234
/download https://pan.quark.cn/s/xxxxxx
/download https://github.com/xxx/xxx/releases/download/v1.0/file.zip
```

> 提取码格式固定为 `密码:xxxx`，同时兼容 `密码：xxxx`、`提取码:xxxx`、`pwd:xxxx`。

### 完整交互示例

```text
用户：/download https://pan.quark.cn/s/abcdef
Bot ：🔍 正在解析链接（夸克网盘），请稍候…
Bot ：📋 解析成功｜来源：夸克网盘｜共 3 个文件

      1. 工具包.zip | 12.3 MB
      2. 说明.pdf | 860.0 KB
      3. 素材.rar | 45.6 MB

      请回复序号选择要下载的文件：
      · 下载单个：回复 2
      · 批量下载：回复 1,3,4
      · 下载全部：回复 全部
      · 取消任务：回复 取消

      ⏰ 本次任务 120 秒内有效，超时自动作废。
      ⚠️ 单文件上限 200 MB，一次最多选择 5 个文件。
      ⚠️ 不同平台对上传文件大小限制不同，超出限制可能上传失败。

同一发起人：1,3
Bot ：✅ 已接收选择：共 2 个文件，开始下载…
Bot ：⬇️ 正在下载（1/2）：工具包.zip（12.3 MB）
Bot ：📥 工具包.zip 下载中：50%（6.1 MB / 12.3 MB）
Bot ：✅ 下载完成，准备上传：工具包.zip（12.3 MB）
Bot ：[文件] 工具包.zip
Bot ：⬇️ 正在下载（2/2）：素材.rar（45.6 MB）
...
Bot ：🏁 本次任务结束：成功 2 个。临时文件已清理。
```

### 选择规则（重要）

- **只有「同一群 + 同一发起人」的回复才生效**：同群其他人回复序号不会被处理，也不会影响任务。
- 支持单个序号 `2`、逗号分隔批量 `1,3,4`（中英文逗号、顿号、空格均可）、`全部` / `all`。
- 任务一次性有效：被选择后立即失效，需要重新发送指令。
- 超过 `session_timeout` 未选择：自动清空并提示「任务已过期」。
- 回复 `取消` 可主动放弃当前任务（同样仅发起人有效）。
- 私聊发送 `/download`：直接提示「该指令仅限群聊使用」，不执行任何解析。

---

## 配置说明

配置文件：`data/config/astrbot_plugin_link_downloader_config.json`，
也可以在 WebUI「插件 → 插件配置」中可视化编辑。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `github_mirrors` | list | 见下 | GitHub 下载镜像列表，原始地址失败时按顺序轮换 |
| `github_token` | string | 空 | 可选，仅用于提升 GitHub API 速率限制 |
| `network_timeout` | int | `60` | 单次请求 / 单个文件下载的超时秒数 |
| `max_file_size_mb` | int | `200` | 单文件最大体积（MB），`0` 表示不限制 |
| `session_timeout` | int | `120` | 等待用户回复序号的超时秒数 |
| `request_interval` | float | `1.0` | 最小请求间隔，用于控制爬虫频率 |
| `max_batch_files` | int | `5` | 单次批量下载的文件数上限 |
| `platform_upload_limit_mb` | int | `100` | 超过该体积时提前提示「可能上传失败」 |
| `max_depth` | int | `3` | 网盘文件夹递归展开的层数 |
| `user_agent` | string | 空 | 自定义 UA，留空使用内置桌面浏览器 UA |
| `baidu_cookie` | string | 空 | 可选，百度网盘登录 Cookie（大文件下载几乎必需） |
| `quark_cookie` | string | 空 | 可选，夸克网盘登录 Cookie（直链下载几乎必需） |
| `use_aiocqhttp_upload` | bool | `true` | QQ 平台优先用 upload_group_file 上传群文件 |
| `keep_failed_temp` | bool | `false` | 上传失败时保留临时文件，便于排查 |

### 默认 GitHub 镜像

```json
[
  "https://ghproxy.net/{url}",
  "https://ghfast.top/{url}",
  "https://gh-proxy.com/{url}",
  "https://hub.gitmirror.com/{url}",
  "https://ghproxy.cc/{url}",
  "https://gh.llkk.cc/{url}"
]
```

- `{url}` 会替换成原始 GitHub 地址；
- 若写成不带占位符的前缀（例如 `https://ghproxy.net/`），插件会自动拼接原始地址；
- 镜像公共服务的可用性随时间变化，失效时请自行替换为可用镜像。

### 关于百度网盘 / 夸克网盘 Cookie

这两个网盘的**大文件实际下载**依赖登录态，匿名请求经常被拒绝（errno / code 非 0）。
若解析成功但下载失败，请填写对应 Cookie：

1. 浏览器登录 `pan.baidu.com` 或 `pan.quark.cn`；
2. F12 打开开发者工具 → Network → 任意请求的 `Cookie` 请求头，整段复制；
3. 粘贴到插件配置的 `baidu_cookie` / `quark_cookie`（密文展示，不会回显）；
4. 保存后重载插件生效。

> Cookie 属于敏感信息，插件不会打印或回显，请勿把它提交到公开仓库。

---

## 实现说明

### 整体流程

```text
/download 链接 [密码:xxx]
      │
      ├─ 私聊？ ── 是 ──> 提示「仅限群聊使用」并结束
      │
      ├─ 正则提取链接 + 提取码
      ├─ build_parser() 按域名挑选解析器
      ├─ parser.list_files()  → 文件清单（名称 / 大小）
      ├─ 注册会话（key = 群号:发送者ID，启动超时任务）
      └─ 输出序号清单

群员回复序号
      │
      ├─ 监听 GROUP_MESSAGE，忽略以 / 开头的指令文本
      ├─ 按 key 查找会话（同群 + 同一发起人）
      ├─ 解析「2 / 1,3,4 / 全部 / 取消」
      ├─ 取出会话（一次性），异步启动下载任务
      └─ 逐个：体积预检 → 下载 → 进度提示 → 上传 → 删除临时文件
```

### 各解析器

| 模块 | 解析方式 |
| --- | --- |
| `lanzou.py` | 抓取分享页 → 判断单文件 / 文件夹 → 解析 `ajaxm.php` 的 sign 等参数换取直链；文件夹用 `filemoreajax.php` 翻页；提取码通过 `?pwd=` 或表单提交校验；直链带时效，失败自动重解析一次 |
| `baidu.py` | 从分享页提取 shareid / uk / sign / timestamp → `/share/verify` 校验提取码 → `/share/list` 拉目录（按 max_depth 递归）→ 下载时优先用 dlink，否则 `/api/sharedownload` 换链 |
| `quark.py` | `/sharepage/token` 换 stoken → `/sharepage/detail` 分页拉目录（递归）→ `/sharepage/download` 用 fid + share_fid_token 换直链 |
| `github.py` | Release 链接调用 GitHub API 列出全部资产（含大小）；Raw 直链转为单文件；下载时 原始地址 → 镜像1 → 镜像2 → … 逐个尝试，超时 / 连接失败 / 状态码异常都会切换下一个 |

### 异步与性能

- 全部网络请求基于 `aiohttp` 异步完成，解析、下载、上传都是协程；
- 下载采用 64KB 分块流式写入，边下边写，不会把整个文件读进内存；
- 全局复用同一个 `ClientSession`，并内置请求限速（`request_interval`）；
- 下载任务在后台 `asyncio.Task` 中运行，指令处理函数立即返回，不阻塞 Bot。

### 临时文件

- 下载目录：`data/plugin_data/astrbot_plugin_link_downloader/tmp/<时间戳_随机串>/`；
- 每个文件上传结束后立即删除；整批任务结束时删除整个任务目录；
- 插件启动时自动清理 24 小时前的残留目录；
- 插件卸载 / 重载时会取消所有后台任务、关闭连接池并清理临时目录。

---

## 常见问题

**1. 提示「解析失败：网页结构可能已发生变化」**
网盘前端改版会导致抓取规则失效，这是网页解析方案的固有风险。
可尝试重新发送指令；若持续失败，需要更新对应 `parsers/xxx.py` 的匹配规则，欢迎提交 PR。

**2. 提取码错误**
请确认格式为 `密码:xxxx`（英文冒号最稳妥），并检查提取码是否复制完整；
百度网盘部分分享的提取码区分大小写。

**3. 百度 / 夸克解析成功但下载失败**
匿名下载受限，请在插件配置中填写 `baidu_cookie` / `quark_cookie`（登录后的完整 Cookie）。

**4. GitHub 下载很慢或超时**
把 `github_mirrors` 换成当前可用的加速镜像；插件会从原始地址开始，
按顺序轮换，直到全部失败才报错。

**5. 文件上传失败 / 群里没有收到文件**
- QQ 群文件有体积限制，请同时调小 `max_file_size_mb`；
- 某些协议端需要 AstrBot 与协议端在同一台机器才能上传本地文件；
- 可尝试开关 `use_aiocqhttp_upload` 切换上传方式。

**6. 私聊发送指令没有反应？**
这是设计行为：插件只处理群聊事件，私聊会直接回复「该指令仅限群聊使用」。

**7. 临时文件会不会越堆越多？**
不会。上传结束即删除，任务结束删除目录，启动时再清理 24 小时前的残留。

---

## 安全与合规

- 本插件仅用于下载**公开分享**的链接，请勿用于绕过付费 / 版权保护等用途；
- 请遵守目标站点的服务条款与所在地区的法律法规，控制下载频率（`request_interval`）；
- 网盘 Cookie 属于个人敏感凭据，插件只保存在本地配置文件中且不会回显，
  请勿把带有 Cookie 的配置文件提交到公开仓库；
- 网页解析方案随时可能因站点改版失效，插件已对所有解析步骤做了异常捕获与友好提示。

---

## 更新日志

### v1.0.0

- 首个版本：群聊 `/download` 指令、四类链接解析、序号选择下载、
  GitHub 镜像轮换、会话超时、体积上限、临时文件自动清理、完整配置面板。
