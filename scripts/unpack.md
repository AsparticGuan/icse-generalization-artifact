# unpack.py 说明

## 1. 功能定位
`unpack.py` 把聚合提交 JSON（含 `fixes` 列表）拆分回逐样本 JSON 文件。

适合：
- 从提交包恢复单样本结果；
- 进行按 issue 的复核与回放。

---

## 2. 核心流程
1. 读取参数：目标目录与输入聚合文件。
2. 创建目标目录（若不存在）。
3. 读取 `data["fixes"]`。
4. 对每条记录按 `bug_id` 写成 `<bug_id>.json`。

---

## 3. 输入输出

### 输入
- 参数 1：`dataset_dir`
- 参数 2：`input_file`（通常由 `submit.py` 生成）

### 输出
- `dataset_dir/<bug_id>.json`

---

## 4. 使用方式

```bash
python3 scripts/unpack.py <dataset_dir> <submission.json>
```

---

## 5. 依赖与关联
- 对偶脚本：`scripts/submit.py`
- 相关脚本：`scripts/pack_jsonlines.py`（另一类打包格式）

---

## 6. 注意事项
1. 若同一 `bug_id` 重复出现，后写入会覆盖前者。
2. 仅做拆分，不做 schema 校验。
3. 输出启用 `indent=2`，便于人工阅读与 diff。
