# AGENTS.md — sgcc-power-ha

> 给 AI 会话与新协作者的项目约定。状态类信息看 README，本文件只记「怎么干活」。

## 一句话

Home Assistant 自定义集成，把「网上国网」账户的用电数据接进 HA。
登录走**网上国网 App 通道**（账号密码 + 短信验证码兜底），
**不碰 95598 网页登录、不需要滑块验证码 / OCR / Docker**。当前 v1.11.0。

## 跑测试

```bash
pip install -r requirements-dev.txt   # pytest / aiohttp / cryptography / voluptuous / gmssl / ruff
pytest -q                             # 期望 51 passed
ruff check .                          # 必须全绿（E / F / I / UP，忽略 E501）
ruff format --check .                 # 期望 31 files already formatted
```

**`gmssl` 必须显式装**：国密 SM3/SM4 实现，签名与测试都要用。
2026-11-03 删掉本仓 `.venv` 后才发现它此前只装在 venv 里，系统解释器没有 → pytest 直接
collection error。所以 `requirements-dev.txt` 里那条 `gmssl>=3.2` 不能删。

## 发版

```bash
# 1 改 version（custom_components/sgcc_power/manifest.json）
git commit -am "..." && git push
git tag v1.11.N && git push origin v1.11.N
gh release create v1.11.N --title "..." --notes "..."

# 2 HACS 更新
haops hacs update yahooor/sgcc-power-ha --version v1.11.N
```

> 本仓**没有打包脚本**（`scripts/` 下只有 `ha_import_smoke.py` 与 `verify_login.py`）。
> `dist/` 是本地打包产物目录，已在 `.gitignore` 里，**不入库**，`git status` 应保持干净。

**固定交付流程**：审查 → 修复 → 推 GitHub → HA 通过 HACS 更新 → 读日志验证。
最后一步不能省——这个集成的登录链路依赖服务端，**只有读 HA 日志才能确认改动没把登录搞坏**。

## 辅助脚本

```bash
python scripts/verify_login.py        # 独立验证登录链路是否通（改 api.py/crypto.py 后先跑它）
python scripts/ha_import_smoke.py     # 导入冒烟测试
```

## 架构要点

| 文件 | 作用 | 注意 |
|---|---|---|
| `api.py` | 网上国网 App 接口客户端 | 改请求/解析必跑 pytest |
| `crypto.py` | 国密 SM3/SM4 签名 | 依赖 `gmssl`；签名算法改了服务端会拒 |
| `login.py` | 登录 + 短信验证码兜底 | 令牌约 15 天有效，过期用已存密码自动重登 |
| `config_flow.py` | 配置流程（含验证码步骤） | 改交互要同步 `strings.json` / `translations/` |
| `coordinator.py` | 轮询协调 | `iot_class: cloud_polling` |
| `sensor.py` | 传感器实体 | — |
| `statistics.py` | 统计聚合（依赖 `recorder`） | `after_dependencies: ["recorder"]` |
| `synthetic_device.py` | 造测试用设备 | 改它不影响运行时 |
| `compat.py` | 兼容层 | 与旧版 state_grid 系列的对齐 |

## 红线

- **不要引入 `state_grid` 系的 95598 网页登录链路**——那条自 2026 年起已失效，
  旧集成的表现是「集成还在跑、数据停在最后一次成功刷新」，比直接报错更难排查
- **不要用 `0.0` 表达「查不到」**——这是本系列集成历史上的大坑，会被误读成「没用电」
- `requirements` 里锁死 `gmssl==3.2.2`（manifest 中），升级前先跑 `pytest`
- `dist/` 是打包产物目录，不入库（`git status` 应保持干净）
- `pyproject.toml` 的 `[project] version` 是 1.0.0，**与 manifest 的实际版本无关**，
  不要拿它当版本号来源——版本号唯一看 `custom_components/sgcc_power/manifest.json`
