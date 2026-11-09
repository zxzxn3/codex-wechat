# 开源前检查清单

> 适用场景：把 `zxzxn3/codex-wechat` 从私有仓库转为公开仓库。  
> 最近审计：2026-11-09。P0 未全部完成前不要切换仓库可见性。

## 当前结论

- [ ] 可以公开
- [x] `LICENSE` 已替换为 Apache License 2.0（当前工作区，待提交）
- [x] 已增加 `NOTICE` 与 `TRADEMARKS.md`（当前工作区，待提交）
- [ ] 当前文件仍包含临时 fine-grained PAT
- [ ] Git 历史中有 4 个提交包含该 PAT
- [ ] README、INSTALL、DISTRIBUTION 仍描述私有仓库和内测入口
- [x] 标签已完成初步盘点：72 个标签中 68 个可从 `main` 到达，4 个仅存在于标签，
      标签独有提交 5 个；未发现敏感路径，但保留策略尚未确定

## P0：密钥、隐私与历史

- [ ] 立即撤销或轮换当前 PAT；仅从新文件中删除不够
- [ ] 从 `README.md`、`docs/INSTALL.md` 删除 PAT 和私有安装命令
- [ ] 按“不重写历史”的约束决定 PAT 处理：立即撤销/轮换 PAT，并评估公开后保留
      历史凭据是否可接受
- [ ] 若不能接受历史中的 PAT，则另建干净公开仓库，或改变“不重写历史”的约束
- [ ] 对全部历史运行密钥扫描，例如
      `gitleaks detect --source . --log-opts="--all"`
- [ ] 在完成完整扫描前，不把 `git log -S` 的初步结果当作“历史已干净”
- [ ] 确认 `.env`、`config.json`、`.state/`、`logs/`、`workspace/`、`releases/`、
      `copyright/`、私钥和证书从未进入任何提交
- [ ] 检查提交作者和提交者邮箱是否符合公开意愿
- [ ] 检查截图、二维码、微信昵称、头像、路径和终端输出是否包含隐私信息
- [ ] 检查 Git LFS、release 附件和 Actions artifact 中没有敏感材料

## P0：许可证与权利

- [x] 根目录使用 Apache License 2.0
- [x] README 许可证徽章和 License 区已更新
- [x] 增加 `NOTICE`
- [x] 增加 `TRADEMARKS.md`
- [ ] 确认 `NOTICE` 中的版权人：当前写的是旧许可证里的 `zxzxn3`，尚未确认真实姓名、
      是否有两位权利人，或是否应写成项目贡献者集合
- [ ] 确认代码、Logo、角色图片、微信截图、字体和图标有权随仓库发布
- [ ] 对品牌图片单独标明授权范围；当前 `codex-wechat-pixel-mascots.png` 和
      `codex-wechat-wechat-contact.jpg` 都没有来源、作者或素材许可记录，且截图包含微信 UI
      和角色头像
- [ ] 品牌素材来源已定位：GPT 生成变体来自 `a-fat-fish`，但源 README 记录的是
      **CC BY-NC-SA 4.0**，且 GPT 参考图来源/许可未完全确认；公开前需确认可接受的单独素材许可，
      或替换为完全自有素材
- [ ] 补录具体 Bilibili 投稿地址、作者和标题；当前只记录了角色来源声明，逐图来源仍未记录
- [x] 运行时依赖初步扫描：`certifi` MPL-2.0、`cryptography` Apache-2.0/BSD-3-Clause、
      `platformdirs` MIT、`psutil` BSD、`qrcode` BSD、`requests` Apache-2.0、
      `curl-cffi` MIT；未发现与 Apache-2.0 明显冲突
- [ ] 完成传递依赖、Windows 运行时、打包二进制和随包字体的许可证审计
- [ ] 如需申请专利，在公开专利相关实现前先提交申请
- [ ] 接受外部贡献前确定 DCO 或 CLA；若未来保留商业双许可，优先 CLA
- [ ] 确认项目名使用 Codex/WeChat 等第三方标识的风险和免责声明

## P0：公开安装与更新

- [ ] 删除“仓库保持私有”“内测 PAT”“私有仓库安装”等公开页面文案
- [ ] 提供无需 GitHub 认证的 Linux 和 Windows 一行安装命令
- [ ] 更新 `docs/INSTALL.md`，从私有 PAT 流程改成公开下载流程
- [ ] 更新 `docs/DISTRIBUTION.md`，说明公开仓库、release 和更新源
- [ ] 在全新 Linux 用户环境测试安装、启动、更新和卸载
- [ ] 在全新 Windows 用户环境测试安装、启动、更新和卸载
- [ ] 验证公开 URL、release 指针和 bootstrap 在未登录 GitHub 时可访问
- [ ] 验证安装失败、网络失败和重复安装时不会泄露凭据或破坏已有数据

## P1：仓库历史与引用

- [x] 标签盘点：61 个 `win/*`、4 个 `experiment/*`、3 个 `keep/*`、2 个 `archive/*`、
      1 个 `windows/*`、1 个 `linux-sync-*`
- [ ] 决定保留哪些标签；不重写标签内容，建议只保留正式 release 标签，删除开发、实验和回滚标签
- [ ] 审查旧分支、归档标签和实验历史，确保没有私有代码或材料
- [ ] 决定是否保留 Windows 开发、实验和回滚历史；不保留则清理引用
- [ ] 确认 `main` 是唯一默认分支，旧分支不会随公开仓库暴露
- [ ] 检查历史中的大文件、构建产物、日志、截图和内部文档
- [ ] 公开前做完整备份，并记录改写前后的提交边界

## P1：安全与信任

- [ ] 增加 `SECURITY.md`，提供私密漏洞报告渠道和支持范围
- [ ] 在文档中说明本机会执行命令、审批、微信绑定凭据和 prompt injection 风险
- [ ] 开启 GitHub secret scanning、push protection 和 Dependabot alerts
- [ ] 检查 Actions 权限最小化；第三方 Action 固定版本或 SHA
- [ ] 检查自动图表提交不会触发提交循环或扩大写权限
- [ ] 增加持续集成：完整单元测试、`ruff`、文档生成检查和依赖审计
- [ ] 考虑 CodeQL 或等价的静态安全扫描

## P1：公开仓库材料

- [ ] README 增加真实截图或 GIF，展示一个完整用户场景
- [ ] 确认 README 仍准确描述隐私、平台、实验性和未验证边界
- [ ] 增加 `CONTRIBUTING.md`
- [ ] 增加 `CODE_OF_CONDUCT.md`
- [ ] 增加 issue、PR 模板和 `SUPPORT.md`
- [ ] 增加 release notes / changelog 规则
- [ ] 设置仓库描述、topics、社交预览图和官网链接
- [ ] 明确维护者、发布负责人和安全响应人

## 最终公开步骤

- [ ] 完整备份本地仓库和远端引用
- [ ] 按“不重写历史”的约束确认历史凭据已撤销，或改用干净公开仓库
- [ ] 重新 clone 一个干净副本，运行完整测试和公开安装烟测
- [ ] 检查 GitHub 许可证识别、README 渲染、图片和 Actions 状态
- [ ] 最后才把仓库可见性改为 public
- [ ] 公开后继续监控 secret scanning、issues 和安全报告
