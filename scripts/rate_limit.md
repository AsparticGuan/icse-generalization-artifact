# rate_limit.py 说明

## 1. 功能定位
`rate_limit.py` 是一个轻量工具，用于查看 GitHub API 当前限流状态。

常用于长时间批量抓取前的预检查。

---

## 2. 功能行为
1. 读取 `LAB_GITHUB_TOKEN`。
2. 创建带认证头的 `requests.Session`。
3. 请求 `https://api.github.com/rate_limit`。
4. 以格式化 JSON 打印结果。

---

## 3. 使用方式

```bash
python3 scripts/rate_limit.py
```

---

## 4. 依赖与关联

### 直接依赖
- `requests`

### 关联脚本
- `scripts/extract_from_issues.py`（同样依赖 GitHub API 限流窗口）

---

## 5. 注意事项
1. 未配置 Token 时，速率上限会显著降低。
2. 脚本只负责展示，不做重试/等待逻辑。
3. 输出可直接用于日志系统或命令行管道。
