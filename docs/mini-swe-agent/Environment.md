# Environment

# mini-SWE-agent v2 的 Environment（环境）到底怎么用

在 mini-SWE-agent 里，**Environment 就是“执行器”**：

模型/agent 只负责“决定要跑什么命令”，而 **Environment 负责“在哪里、用什么方式把命令跑出来，并把输出抓回来”**。

所以你把它理解成：**agent 的手和脚**（agent 给动作，environment 落地执行）。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/environments/))

---

## 1) 你会在什么时候“用到” Environment

你主要会在两处“指定/切换”环境：

1. **命令行**：用 `-environment-class` 选择环境实现（local/docker/singularity/…）。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/environments/))
2. **YAML 配置文件**：在 `environment.environment_class` 里选环境，并写该环境的参数（cwd/timeout/env/…）。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

mini CLI 默认用 **local**（本机直接跑）。

但做 SWE-bench 这种评测时通常要隔离，因此更常用 docker / singularity / SWE-ReX。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/environments/))

---

## 2) Environment 的“统一接口”长什么样（结合实现讲原理）

无论你选哪种环境类，它们都在干同一件事：

- 接收 agent 生成的一个 `action`（通常包含 `command`）
- 决定工作目录 `cwd`
- 执行命令（本机 subprocess / docker exec / singularity exec / bwrap / SWE-ReX runtime）
- 返回结构化结果：

```python
{
	"output": "...", 
	"returncode": 0/非0, 
	"exception_info": "...", 
	"extra": {...可选...}
}
```

你从实现里能看到：

各环境都用 `action.get("command","")` 取命令，然后执行，最后返回上述 dict。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/local/))

### 一个关键“小机关”：提交/停止信号

Local/Docker 等环境里都有 `_check_finished()`：

如果输出第一行是 **`COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`** 且 returncode=0，就抛出 `Submitted` 异常，通知 agent **立刻结束并把后续内容当最终提交**。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/local/))

这就是为什么很多 SWE-bench 配置要求用固定提交命令（echo sentinel 再 cat patch）。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

## 3) 各种 environment_class 怎么选：它们“差异点”是什么

官方列出的环境类（你贴的那几种）核心差异在于：

**隔离程度、可用平台、执行后端**。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/environments/))

下面我按“你最关心的维度”讲清楚。

---

## 4) local（LocalEnvironment）：本机直接执行（最快、最不隔离）

### 4.1 适用场景

- 你在自己机器上快速试跑、调试、做小任务
- 你确认代码执行不会污染系统/不会跑危险命令

### 4.2 运行机制（实现细节）

- 直接 `subprocess.run(..., shell=True, cwd=..., env=os.environ | self.config.env)`
- **没有隔离**：读写你本机文件、使用你本机 Python 环境。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/local/))

### 4.3 最小配置与例子

**CLI 直跑（示意）**

```bash
mini "把 a.py 里的 bug 修了" --environment-class local
```

**YAML（local 只接受 cwd/env/timeout 这类字段）**

```yaml
environment:
  environment_class: local
  cwd: "/path/to/repo"
  timeout: 60
  env:
    PAGER: cat
```

> 注意：如果你把 `interpreter` 之类 docker 专用字段写到 local 里，通常会因为配置校验（pydantic）报错（字段不匹配）。
> 

---

## 5) docker（DockerEnvironment）：用 docker exec 在容器里跑（评测最常用）

### 5.1 适用场景

- SWE-bench / 可复现实验：需要隔离 & 固定依赖
- 你本机有 Docker

### 5.2 运行机制（实现细节：非常关键）

DockerEnvironment **先启动一个“睡眠容器”**，然后每条命令用 `docker exec` 执行：

1. 启动容器（核心是 run + sleep 保活）：
- `docker run -d ... <image> sleep <container_timeout>` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/docker/))
1. 执行命令：
- `docker exec -w <cwd> [-e KEY=VAL ...] <container_id> <interpreter...> <command>` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/docker/))

### 5.3 DockerEnvironmentConfig 里你最常改的参数

- `image`：容器镜像（必填）
- `cwd`：容器内工作目录
- `timeout`：单条命令超时
- `env`：强制设置进容器的环境变量
- `forward_env`：从宿主机转发指定变量（**仅当宿主机该变量存在才会传**，且与 `env` 冲突时 `env` 优先） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/docker/))
- `interpreter`：默认 `["bash","-lc"]`，也就是常见的 `bash -lc "<cmd>"`（你可改成 `["bash","-c"]` 来避免 login shell） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/docker/))

### 5.4 一个实际 YAML 例子（文档里就有类似片段）

```yaml
environment:
  cwd: "/testbed"
  timeout: 60
  interpreter: ["bash", "-c"]
  env:
    PAGER: cat
    LESS: -R
  environment_class: docker
```

这段来自 YAML 配置文档的示例片段，展示了 `environment_class: docker` 以及常用的 cwd/timeout/interpreter/env。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

## 6) singularity（SingularityEnvironment）：HPC 常见替代 Docker（Apptainer）

### 6.1 适用场景

- 学校/HPC 集群不让用 Docker daemon
- 你有 Singularity/Apptainer

### 6.2 运行机制（实现细节）

它会先把 image **build 成 sandbox**（并带重试），然后每条命令用 `singularity exec` 跑：

- build sandbox：`singularity build --sandbox <dir> <image>`，失败会重试并清理目录。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/singularity/))
- exec：拼出 `singularity exec ... --pwd <workdir> --env KEY=VAL ... --writable <sandbox_dir> bash -c <command>` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/singularity/))
- `executable` 默认来自环境变量 `MSWEA_SINGULARITY_EXECUTABLE`，否则用 `"singularity"`。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/singularity/))

### 6.3 最小配置示意

```yaml
environment:
  environment_class: singularity
  image: "docker://python:3.11"  # 或你已有的 sif / 镜像来源
  cwd: "/"
  timeout: 60
  forward_env: ["HTTP_PROXY", "HTTPS_PROXY"]
  env:
    PAGER: cat
```

（字段名与行为见其 Config 定义。） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/singularity/))

---

## 7) bubblewrap（BubblewrapEnvironment）：Linux 轻量无特权沙箱（实验性）

### 7.1 适用场景

- Linux 上想要“比 local 安全一点”、又不想/不能用容器
- 接受实验性（文档明确标注 experimental & 非 Windows 支持） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/bubblewrap/))

### 7.2 运行机制（实现细节）

- 用 `bwrap` 加一组默认 `wrapper_args`（只读绑定 /usr /bin /lib /etc，tmpfs /tmp，挂 proc/dev，设置 PATH 等） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/bubblewrap/))
- 执行时会 `-bind <cwd> <cwd> --chdir <cwd>`，再 `bash -c <command>`，并把 `env` 用 `-setenv` 注入。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/bubblewrap/))
- 每个环境实例还会创建独立的临时 working_dir。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/bubblewrap/))

---

## 8) SWE-ReX：swerex_docker / swerex_modal（面向规模化评测/训练）

### 8.1 swerex_docker（SwerexDockerEnvironment）

- 仍然是 Docker，但通过 SWE-ReX 的 `DockerDeployment` + runtime 执行 `RexCommand`，更偏向“评测/训练流水线”集成。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/swerex_docker/))

你能在实现里看到：

- 初始化：`DockerDeployment(image=...)` 然后 `deployment.start()`
- 执行：`deployment.runtime.execute(RexCommand(command=..., shell=True, cwd=..., timeout=...))` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/swerex_docker/))

### 8.2 swerex_modal（SwerexModalEnvironment）

- 把执行搬到 Modal 的 sandbox（云端规模化跑很多 worker）。
- 需要 `pip install "mini-swe-agent[full]"` + `modal setup`。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/swerex_modal/))
- 文档给了用 `swebench_modal.yaml`、`-workers 100` 的例子。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/swerex_modal/))

---

## 9) 三个“最实用”的选择建议（快速决策）

- **我就想本机先跑通**：`local`
- **我要可复现、隔离、跑 SWE-bench**：`docker`（最常用） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/environments/))
- **我在 HPC/没 Docker**：`singularity`（或 Linux 上试 `bubblewrap`） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/environments/singularity/))

---