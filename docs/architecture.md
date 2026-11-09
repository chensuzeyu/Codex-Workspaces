# 架构与功能覆盖

| 模块 | 职责 |
|---|---|
| config、environment | 私有配置、资源冲突校验、账号和代理环境、个人包装入口 |
| tmux | 按 profile 的命名 socket、所有权校验、session、窗口、滚轮和历史 |
| network、runner、services | 独立后台代理和健康守护进程、端口检查、子进程自动重试、私有轮转日志 |
| proxy | Mihomo 配置、订阅缓存、节点过滤、健康评分、切换、回滚和控制 |
| ssh | 公钥绑定、客户端配置、受管理 dispatcher、预演与回滚 |
| probe、app_probe | 实际 CLI 和 App Server 请求、完成事件、私有证据 |
| migration、audit | 旧配置只读导入、开源发布扫描 |

保留的使用能力包括：多账号路径与认证隔离；共用程序但分开配置；独立代理端口与状态；手动或自动选点；香港名称过滤；服务自动恢复；命名 tmux、连接及状态；默认 tmux 滚轮显式配置；SSH key 路由；CLI 和 App Server 探测；异常拒绝接管；重建后重装；保存证据；私有日志轮转。

现有服务不会被自动迁移。旧数据目录可在私有配置中继续引用，但两个管理器不能同时占用同一个代理端口或管理同一 socket。导入工具只准备映射，不执行切换。

健康检查的结果是某一时刻的样本，不保证未来网络稳定。共享 Unix 用户拥有相同文件权限；对于权限隔离，使用独立 Linux 用户。

## 0.2 主链路

SSH 公钥 → CWS_SSH_PROFILE → dispatcher → profile → CODEX_HOME / 文件凭据 / 独立代理 → CLI 或 App Server。

代理和健康守护由 network 启动，runner 负责子进程恢复、清理和日志；services 使用临时运行目录中的 PID 与进程启动时间核对所有权，通过绑定进程启动时间的停止请求触发自行清理退出。网络不依赖 tmux。状态就绪不等于真实模型请求成功。

手动 tmux 仅是可选终端容器。继承身份优先，缺失时读取当前窗格的会话环境，拒绝未知身份。兼容的个人受管理 tmux 继续使用固定 profile；普通共享 tmux 会话不应由不同账号共用。

平台托管公钥覆盖的限制和 `ExposeAuthInfo` 方案的实现边界见 [平台 SSH 身份说明](platform-ssh-identity.md)；当前公开版仍使用 `CWS_SSH_PROFILE` 公钥选项。
