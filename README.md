# 国家电网电力（sgcc_power）

将“网上国网”账户中的用电数据接入 Home Assistant 的社区集成。针对旧版
state_grid 系集成自 2026 年起 **95598 网页登录链路失效、无法获取数据** 的问题
重新实现：登录改走网上国网 App 通道——不需要浏览器、不需要 OCR/大模型识别
验证码、不需要 Docker，账号密码即可完成配置。

> 这是社区维护的非官方集成，与国家电网有限公司没有隶属或授权关系。它依赖
> 网上国网 App 的非公开接口，服务端升级后可能暂时失效。请勿用于计费、结算
> 或其他需要法律效力的场景。南方电网账户不适用。

## 为什么旧集成会登录失败

旧版集成（bilezhou / lxg20082008 一系）通过 95598 网上营业厅网页登录，
需要自动求解滑块验证码。国网页面多次改版后，旧登录体携带的字段已被服务端
直接拒绝，叠加按登录标识的风控判定，自动登录大面积失效——集成本身还在运行，
但数据停留在最后一次成功刷新。本集成不再触碰网页登录链路。

## 功能

- 使用网上国网手机号和密码登录（App 通道，无验证码环节）
- 服务端要求新设备验证时，按提示输入一次 6 位短信验证码
- 登录令牌约 15 天有效，过期自动用已保存的密码重新登录；剩余不足两天即
  主动续期，避免服务端提前作废令牌导致的数据断流
- 支持一个账户下的多个用电户号，每户号独立设备与传感器
- 每户号 17 个传感器：昨日 / 本月 / 上一结算月 / 本年电量与峰、谷、平、尖
  分时拆分，上一结算月电费、本年累计电费、账户余额 / 应交金额、上月抄表读数
- “本月用电量”与“本年累计电量”带 `total_increasing`，可直接进能源面板
- 昨日用电量带 `daily_history`、上一结算月电量带 `monthly_bill_history` 属性，
  供卡片直接取历史序列
- **旧版兼容层**：成功刷新后同步写入 `.storage/state_grid.config`（与旧版
  state_grid 集成同构），让 state_grid_info 等辅助集成及其卡片继续有新数据，
  可在选项中关闭

## 安装

### HACS

1. HACS → 集成 → 右上角菜单 → 自定义存储库
2. 添加 `https://github.com/yahooor/sgcc-power-ha`，类别 **集成**
3. 搜索“国家电网电力”并下载，重启 Home Assistant

### 手动安装

1. 从 [Releases](https://github.com/yahooor/sgcc-power-ha/releases) 下载 zip
2. 将 `custom_components/sgcc_power` 复制到 HA 配置目录的 `custom_components`
3. 重启 Home Assistant

## 配置

1. 设置 → 设备与服务 → 添加集成 → 搜索“国家电网电力”
2. 输入网上国网手机号和密码
3. 如收到新设备安全验证短信，继续输入 6 位验证码

选项中可调整：查询最近几个月（1–3，默认 2）、刷新间隔（6–24 小时，默认 12）、
旧版兼容文件开关（默认开）。

**建议**：装好本集成并确认有数据后，把旧的 state_grid 集成条目禁用或删除，
避免两个集成同时写 `.storage/state_grid.config`。state_grid_info 无需改动，
其“HassBox 数据文件”来源会继续读到新数据。

## 传感器

| 传感器 | 单位 | 说明 |
|---|---:|---|
| 昨日用电量（及峰/谷/平/尖） | kWh | 上游最新发布日的用量与分时拆分，`latest_date` 属性为准 |
| 本月用电量（及峰/谷/平/尖） | kWh | 本月累计；分时值由每日记录汇总 |
| 上一结算月电量 / 电费 | kWh / CNY | 最近已出账周期，不是本月实时值 |
| 本年累计电量 / 电费 | kWh / CNY | 结算口径的年度累计 |
| 账户余额 | CNY | 预付费余额；后付费账户显示可用结余 |
| 应交金额 | CNY | 后付费账户当前应交；预付费为 `unknown` |
| 上月抄表读数 | — | 最近结算月末读数，部分地区不提供 |

上游缺失的数据保留为 `unknown`，不会填 0。当天电量通常延后一天发布，
属正常上游延迟。

## 登录与短信验证

- 令牌失效后自动用已保存的密码重登；仅在服务端明确要求新设备验证时发短信
- 极少数账号 / 网络环境可能收到安全校验未通过（RK008），此时请稍后在
  重新认证提示中重试，或先在官方 App 完成验证并更换密码
- 为支持自动重登，手机号与密码保存在 HA 的 `.storage/core.config_entries`，
  请自行评估可接受性

## 致谢与许可

- [stevenjoezhang/hass-state-grid](https://github.com/stevenjoezhang/hass-state-grid)（MIT）——
  App 通道协议实现、设备身份与图灵盾令牌生成
- [tiejiang29/state_grid](https://github.com/tiejiang29/state_grid)（MIT）——
  令牌提前作废的应对与请求节流经验
- [bilezhou/state_grid](https://github.com/bilezhou/state_grid)（MIT）与
  [lxg20082008/state_grid](https://github.com/lxg20082008/state_grid)——
  传感器口径与旧版存储格式
- [xiaoshi930/state_grid_info](https://github.com/xiaoshi930/state_grid_info)——
  兼容层所服务的辅助集成

MIT License，见 [LICENSE](LICENSE)。
