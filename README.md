# Codex Workspaces

在同一台 Linux 主机上管理多个 Codex CLI 账号。每个 profile 使用独立的 `CODEX_HOME`、工作目录和可选后台代理；SSH 客户端也可以按公钥选择 profile。

公开源码不包含实际账号、主机地址、公钥、订阅或运行数据。私有配置和状态放在源码目录之外。使用同一个 Linux 用户时，这是一套使用约定，不是文件权限隔离。

## 安装和快速开始

需要 Linux、Python 3.11+、Bash 和已安装的 Codex CLI。代理功能还需要独立安装 Mihomo。客户端可以从 Windows、macOS 或 Linux 使用 SSH 连接。

```bash
python3 -m venv ~/.venvs/codex-workspaces
. ~/.venvs/codex-workspaces/bin/activate
python -m pip install .
codex-workspace init
```

初始化会在 `~/.config/codex-workspaces/config.json` 创建私有配置，包含 `alice` 和 `bob` 两个示例；不会复制或创建登录凭据。编辑该文件，指定每人的 `codex_home`、`cwd` 和真正的 `codex_bin`。可参考 `examples/config.example.json`，并将配置保存在仓库之外。

```bash
codex-workspace validate
codex-workspace start alice
codex-workspace run alice --
# 安装 SSH dispatcher 后，也可直接执行 codex
```

`start` 只启动个人网络后台，不创建 tmux。普通 CLI 和桌面 App Server 都会自动确保本人的网络服务启动。`status all` 检查后台和网络；`cws` 是短别名。代理未启用时不需要 tmux，也不启动服务。

Codex 使用个人 `CODEX_HOME` 和文件认证，清除继承的认证变量。默认使用 `--no-alt-screen`；不支持该参数的 CLI 可设置 `"inline": false`。工作目录不决定账号。

## 独立代理和自动选点

为每个人在私有配置中设置不同的 `mixed_port`、`controller_port`、`mihomo_bin`，并设 `proxy.enabled` 为 `true`。`runtime_dir` 可省略，默认位于各自的私有状态目录。程序校验账号目录、socket、代理端口和运行目录是否冲突，只允许回环监听。

```bash
# 通过隐藏输入提供订阅 URL；也可使用 --url-file 和 --source-file 指向私有文件
codex-workspace proxy alice provider register primary
codex-workspace proxy alice provider use primary
codex-workspace network start alice
codex-workspace network status alice
codex-workspace proxy alice control nodes
codex-workspace proxy alice control auto
```

首次在代理尚未运行时，`provider use` 会验证并准备配置；代理运行后切换会热加载、探测并在失败时恢复。支持 provider 的 `list`、`preview`、`register`、`refresh`、`check`、`use`，以及节点的 `status`、`nodes`、`pin`、`auto`、`refresh`。

每个 profile 有两个脱离终端的守护进程，分别运行代理和自动选点。子进程退出后自动重试，日志按大小轮转。`network stop alice` 只停止本配置中本人的已验证守护进程及其子进程，不操作 tmux。并发启动使用独立文件锁；端口被未知进程占用时拒绝接管。SSH 断开或关闭 tmux 不影响服务。未安装开机任务，实例重启后再次运行个人入口即可恢复。守护进程被强制杀死后可能残留子进程，此时应人工检查占用，不能直接接管。

选点读取 Codex 专用健康检查，默认排除香港和订阅提示行，按节点名称筛选，并使用失败率、延迟、冷却和保持期选择出口。10 秒是默认检测周期，不是故障恢复时间保证。节点来源、订阅 URL 和节点凭据均保存在私有运行目录，不应提交到 Git。

## 桌面客户端 SSH

在每个 profile 的私有配置中设置 `ssh.public_key_file`。每个人使用不同的公钥；同一把私钥复制到多台电脑仍代表同一个身份。

```bash
# 先生成操作计划；检查 sshd 的 PermitUserEnvironment 和目标文件
codex-workspace ssh install --authorized-keys ~/.ssh/authorized_keys --dispatcher ~/.local/bin/codex
# 审阅后执行；需要对目标文件有写权限
codex-workspace ssh install --authorized-keys ~/.ssh/authorized_keys --dispatcher ~/.local/bin/codex --apply
# 为本机 SSH 配置生成示例
codex-workspace ssh client-config alice --host 198.51.100.10 --port 22 --user developer
```

**平台托管公钥注意：** 当前公开版仅实现公钥行的 `environment="CWS_SSH_PROFILE=..."` 方式；平台若重写授权文件，身份绑定会丢失。另有基于 `ExposeAuthInfo` 和 `SSH_USER_AUTH` 的部署思路，但**本开源版尚未实现或自动安装它**。原理、限制和验证边界见 [平台托管公钥说明](docs/platform-ssh-identity.md)。

管理员需事先允许 SSH 接受 `CWS_SSH_PROFILE`；工具不会修改或重启 sshd。确保 dispatcher 所在目录在远程登录 shell 的 `PATH` 中。安装器保留无关公钥，拒绝接管其他项目的 dispatcher，并在私有状态目录保存回滚备份。它不会修改由平台托管的只读公钥来源；平台覆盖授权文件或实例重建后需重新检查和安装。

将生成的别名添加到各自电脑的 `~/.ssh/config`，`IdentityFile` 指向与该用户公钥匹配的私钥，然后在客户端 **设置 → 连接 → SSH** 选择自己的别名和远程项目目录。客户端启动自己的 App Server，不需要 attach 到 tmux；不要把 tmux attach 配成 SSH RemoteCommand。

## 可选 tmux 与身份传递

普通使用不需要 tmux。习惯 tmux 的用户可在个人 SSH 连接后创建自己的会话。为手动创建的会话安装配置：

```bash
codex-workspace tmux-config > ~/.config/codex-workspaces/tmux.conf
# 将下面这行加入 ~/.tmux.conf（不要覆盖已有配置）：
# source-file ~/.config/codex-workspaces/tmux.conf
# 已运行的服务也需显式加载：
tmux source-file ~/.config/codex-workspaces/tmux.conf
```

配置启用滚轮，并让新建/重新连接会话传递 `CWS_SSH_PROFILE`。已有窗格优先使用继承的 SSH 身份；缺少时，dispatcher 仅查询该窗格所属会话的环境，不读取共享服务器的全局身份，不根据会话名或目录猜账号。旧会话需用自己的 SSH 连接重新 attach 一次；不要加 `-E`。不同账号不要共用一个会话：再次连接可能更新会话环境，这不是永久的创建者绑定，也不是权限隔离。

原 `attach alice`、`tmux alice -- ...` 保留为可选兼容命令，继续使用有所有权标记的个人 socket，账号固定为显式指定的 profile。它们不承载后台网络服务。`scroll` 仍只修改滚轮配置，不修改身份路由。历史只保存在 tmux 内存；服务停止不保留终端历史。

## 验证

```bash
# 明确选择旧的 default 服务；可选 --persist ~/.tmux.conf 为以后启动保留设置
codex-workspace scroll --socket default
# 一个简短的真实账号模型请求，使用少量额度
codex-workspace probe alice
# App Server 协议与模型请求；--ssh 可指定已有 SSH 别名
codex-workspace probe alice --transport app-server
```

探测要求认证、预期回复及完成事件；HTTP 405 仅代表健康检查目标可达。证据写入该 profile 的私有状态目录。源代码测试使用临时目录和模拟账号，不读取个人凭据。

## 从旧工作区迁移

```bash
codex-workspace migrate --from /path/to/legacy-workspace \
  --output ~/.config/codex-workspaces/imported.json \
  --state-dir ~/.local/state/codex-workspaces-imported
codex-workspace --config ~/.config/codex-workspaces/imported.json validate
```

从 0.1 升级时不会自动停止旧 `net-mihomo` / `net-health` 窗口。先用旧版 `network stop <profile>` 停止旧网络，再用新版启动；否则新版会拒绝已占用的端口。不要直接结束有工作任务的 tmux 服务。

迁移只读取旧映射并写私有配置，不接管或停止现有服务。导入的端口可能被旧服务占用：先检查生成配置，选择新的端口或单独计划切换；启动时遇到占用会拒绝接管。运行中的代理配置、凭据、订阅和日志不会进入公开仓库。

## 开发与发布

```bash
python -m unittest discover -s tests -v
codex-workspace audit --source .
codex-workspace audit --source . --staged
```

可设置 `CWS_TEST_MIHOMO` 为本机 Mihomo 二进制路径，增加使用本地模拟节点的真实引擎集成测试。自动扫描覆盖常见密钥、凭据 URL、非示例 IP 和误入暂存区的运行数据，仍需人工审阅发布内容。

架构和功能清单见 [docs/architecture.md](docs/architecture.md)；贡献和提交格式见 [CONTRIBUTING.md](CONTRIBUTING.md)。MIT 许可见 [LICENSE](LICENSE)，代理后端来源见 [NOTICE](NOTICE)。
