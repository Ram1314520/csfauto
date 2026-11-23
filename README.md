# CSFloat 自动发货

纯 Python 的 CSFloat → Steam 自动发货服务。程序直接使用 CSFloat API 与 Steam 会话，不需要长期打开 CSFloat 或 Steam 网页。

当前版本固定使用 `aiosteampy 0.7.21`，默认处于 `dry_run`，不会接受订单、创建报价或确认 Steam 移动令牌。项目以 Python 3.12 为主要测试和部署版本。

> 该程序会操作真实数字物品。第一次上线必须依次完成 dry-run、单笔低价值订单和重复运行检查。

## 工作流程与安全保护

每个订单独立处理：

1. 使用 CSFloat API Key 查询 `queued/pending` 卖家订单。
2. 验证卖家 SteamID、买家 SteamID、交易链接 partner/token 和资产 ID。
3. 根据 `steam_offer.id/state` 区分新订单、可恢复报价、已完成报价和异常报价。
4. 使用 `/trades/bulk/accept` 接受单个新订单。
5. 从 Steam 库存精确获取订单资产。
6. 创建一个只给出该订单资产、且不收取任何资产的 Steam 报价。
7. 重新读取报价，严格验证发送方、接收者和完整资产集合。
8. 向 CSFloat `/trades/steam-status/new-offer` 上报准确的 `offer_id`。
9. 只确认该 `offer_id` 对应的 Steam 移动确认。
10. 确认 CSFloat 显示相同 `offer_id` 后，将卖家侧发货标记为完成。

CSFloat 在等待验证或结算时仍可能把已发送的订单返回为 `pending`。程序不会仅凭 `pending` 判断需要发货：

- 没有 `steam_offer_id`：`action=new`，属于新发货。
- 报价状态为 `ACTIVE` 或 `CONFIRMATION_NEEDED`：`action=resume`，恢复已有报价。
- 报价状态为 `ACCEPTED` 或 `IN_ESCROW`：计入 `completed` 并跳过。
- 其他报价状态：计入 `manual_review`，不会自动创建新报价。

所有进度保存在 `data/state.db`。程序在接受订单、创建报价、上报报价或确认令牌后中断时，会优先恢复已存在的报价，不会直接重复发送。

日志中的“发货流程完成”表示卖家报价已经发送、移动令牌已经确认并已上报 CSFloat；买家仍可能尚未接受报价。

## Windows 本地安装

推荐 Python 3.12。可以和电脑中已有的 Python 3.14 并存，不需要卸载其他版本。

如果尚未安装 3.12：

```powershell
py install 3.12
```

进入项目目录后，逐条执行：

```powershell
py -V:3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

命令行开头出现 `(.venv)` 表示虚拟环境已经激活。`requirements.txt` 已包含 Steam 登录所需的 `protobuf`，不要只单独安装 `aiosteampy`。

## 配置说明

不要把真实密钥粘贴到聊天、日志、截图或 GitHub。编辑本机的 `config.json`：

```json
{
  "mode": "dry_run",
  "live_confirmation_phrase": "",
  "csfloat_api_key": "CSFloat API Key",
  "steam_id64": "17位SteamID64",
  "steam_login": "Steam登录账号名",
  "steam_password": "Steam密码",
  "steam_api_key": "Steam Web API Key",
  "shared_secret": "Base64 shared_secret",
  "identity_secret": "Base64 identity_secret",
  "poll_interval_seconds": 60,
  "proxy": null,
  "steam_proxy": null
}
```

主要字段：

- `steam_login` 是登录账号名，不是个人资料昵称。
- 当前版本的 `live` 配置校验要求填写 `steam_api_key`。
- `shared_secret` 用于生成 Steam Guard 登录验证码。
- `identity_secret` 用于确认 Steam 报价。
- 两个 secret 通常来自对应账号的 SDA `maFile`；它们不是手机显示的五位验证码。
- `poll_interval_seconds` 单位为秒，最低为 30；推荐 60–300。
- `proxy` 仅用于 CSFloat API，支持 HTTP/HTTPS。
- `steam_proxy` 用于 Steam，支持 HTTP/HTTPS/SOCKS4/SOCKS5。

## 第一步：dry-run 只读检查

先保留：

```json
"mode": "dry_run"
```

此阶段只需确保 `csfloat_api_key` 和 `steam_id64` 正确，然后运行：

```powershell
python -m csfauto.main --config config.json --once
```

典型输出：

```text
跳过已发货订单 ... steam_offer_state=3
订单分类 actionable=1 completed=4 manual_review=0 mode=dry_run
DRY-RUN ... state=queued action=new asset_count=1 steam_offer_id=None ...
```

确认：

- `actionable` 只包含真正需要操作的新订单或可恢复报价。
- 已经由买家接受、正在验证或结算的订单位于 `completed`。
- 商品名和资产数量符合预期。
- `manual_review` 为 0；若不为 0，先人工检查，不要切换 live。

## 第二步：单笔真实发货

填写 Steam 登录信息、API Key 和两个 secret，然后设置：

```json
"mode": "live",
"live_confirmation_phrase": "I_UNDERSTAND_THIS_SENDS_REAL_STEAM_OFFERS"
```

确认只有一笔低价值测试订单后运行：

```powershell
python -m csfauto.main --config config.json --once
```

成功流程通常包含：

```text
接受CSFloat订单 ...
创建Steam报价 ...
确认Steam报价 ... offer_id=...
发货流程完成 ... offer_id=...
```

完成后再运行一次相同的 `--once` 命令，确认没有再次出现“创建Steam报价”，以验证重复运行保护。

不要同时点击 CSFloat 网页上的“交易/发送报价”按钮，否则网页与脚本可能同时处理同一订单。

## 第三步：持续运行

移除 `--once`：

```powershell
python -m csfauto.main --config config.json
```

程序按 `poll_interval_seconds` 周期查询。每轮结束会显示：

```text
当前轮已于 2026-11-23 12:42:12 CST 处理完成，等待 60 秒进行下一轮处理
```

使用 `--once` 时只执行一轮，不会显示“等待下一轮处理”。

按 `Ctrl+C` 可安全停止。关闭 PowerShell 窗口也会停止程序。修改 `config.json` 后需要重启程序才能生效。

同一 Steam/CSFloat 账号只能运行一个实例，不要让本机、Docker 和云服务器同时运行。

## Steam 网络与代理

如果出现：

```text
Cannot connect to host steamcommunity.com:443
```

说明程序无法直连 Steam，不是账号密码错误。Python 不一定自动使用 Windows 系统代理，需要显式设置 `steam_proxy`。

例如 Clash 显示 HTTP/Mixed 端口为 `7897`：

```json
"steam_proxy": "http://127.0.0.1:7897"
```

端口必须以代理软件实际显示为准。若 CSFloat 本身可以直连，可继续保留：

```json
"proxy": null
```

可以用 PowerShell 检查直连：

```powershell
Test-NetConnection steamcommunity.com -Port 443
```

## 常见故障与恢复

### live 确认短语错误

若看到：

```text
配置错误：live 模式必须将 live_confirmation_phrase 设置为 I_UNDERSTAND_THIS_SENDS_REAL_STEAM_OFFERS
```

确保以下两项完全一致，注意大写字母、下划线和 JSON 逗号：

```json
"mode": "live",
"live_confirmation_phrase": "I_UNDERSTAND_THIS_SENDS_REAL_STEAM_OFFERS"
```

该短语是防止误发真实报价的安全开关，不应从程序中删除。

### 缺少 protobuf

若看到：

```text
Protobuf library is required for Steam authentication
```

重新安装完整依赖：

```powershell
python -m pip install -r requirements.txt
```

### 库存解析出现 IndexError

Steam 的部分 CS2 检视链接不含旧版库预期的 `%D` 占位符。项目已包含兼容处理，会忽略格式异常且与发货无关的 Inspect action，同时保留库存资产。不要直接修改虚拟环境中的第三方库文件。

### Steam 移动确认返回 HTTP 429

单次正常确认不应持续返回 429。当前版本会使用与 Steamauto 相同的移动客户端请求头，并在遇到 429 时：

- 保留已经创建和上报的 `offer_id`。
- 将订单保持为可重试状态，不消耗普通订单失败次数。
- 不把带临时签名的完整确认 URL 写入数据库。
- 下次轮询恢复同一个报价，不重新创建。

若仍出现 429：

1. 不要连续重试，也不要点击网页发送报价。
2. 等待 5–10 分钟；持续出现时等待 15–30 分钟。
3. 如果使用共享代理节点，切换到稳定节点后再试。
4. 确认服务器时间同步正常。

### Steam API 连接超时

若 Mihomo、代理节点或 Steam 网络短暂波动，程序可能在读取库存、查询已有报价、创建报价或确认报价时遇到连接超时。当前版本会：

- 将这类连接错误标记为 `retryable_error`，等待下一轮自动恢复。
- 不消耗普通订单失败次数，也不会立即进入 `manual_review`。
- 保留已经接受的 CSFloat 订单和已经创建的 Steam `offer_id`。
- 不在日志或数据库中记录包含 `access_token` 的完整异常 URL。

不要因为网络超时手动重新发送报价。先检查代理稳定性，程序会继续恢复同一订单。

### CSFloat 已接受，但报价尚未创建

修复网络或库存问题后重新运行即可。程序会读取 `accepted_at`，不会重复接受订单。

### 报价已创建，但程序中断

不要手动重新发报价。程序会使用 CSFloat 返回的 `steam_offer_id`、本地 `state.db` 或报价订单标记恢复既有报价。

### manual_review

订单连续失败达到 `max_order_attempts`，或遇到无法安全判断的报价状态时，会进入 `manual_review` 并停止自动处理。不要删除 `state.db` 来强制重试，应先确认 CSFloat 和 Steam 中是否已经存在报价。

## Docker 与云服务器

项目不依赖浏览器，可以部署到云服务器。推荐固定公网 IP、稳定线路和准确系统时间。

启动：

```bash
docker compose up -d --build
docker compose logs -f
```

停止：

```bash
docker compose down
```

`docker-compose.yml` 已将以下内容持久化：

- `./config.json` → 容器内只读配置。
- `./data` → Steam Cookie、订单状态数据库和恢复信息。

服务器部署要求：

- 启用 NTP/系统时间同步；Steam 移动确认依赖准确时间。
- 确保能访问 `csfloat.com` 与 `steamcommunity.com`。
- 云服务器上的 `127.0.0.1:7897` 指服务器自身，不是你家中电脑；代理必须部署在服务器可访问的位置。
- 从本机迁移到服务器前，先停止本机实例，再复制项目、`config.json` 和完整 `data/` 目录。
- 不要频繁更换服务器 IP 或代理出口，避免触发 Steam 风控。

## 敏感文件与备份

以下内容均已加入 `.gitignore`，不得上传或分享：

- `config.json`
- `data/steam_cookies.json`
- `data/state.db`
- `data/` 中的数据库备份

建议定期备份整个 `data/` 目录，并限制服务器上 `config.json` 和 `data/` 的读取权限。

默认要求 `steam-status/new-offer` 上报成功后才确认 Steam 报价。如果 CSFloat API Key 无权调用该接口，程序会停止在未确认状态，不会盲目继续。
