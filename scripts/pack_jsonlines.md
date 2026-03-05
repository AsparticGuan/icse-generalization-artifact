# pack_jsonlines.py 说明

## 1. 功能定位
`pack_jsonlines.py` 用于把目录内多个 JSON 文件打包成一个 `.jsonl` 文件。

每个输入 JSON 对象写成输出中的一行，适合：
- 批量训练数据准备
- 评测数据归档
- 传输与流式消费

---

## 2. 输入输出

### 输入
- 参数 1：`dataset_dir`（JSON 文件目录）
- 参数 2：`output_file`（输出 JSONL 路径）

### 输出
- `output_file`：JSON Lines 文件

---

## 3. 使用方式

```bash
python3 scripts/pack_jsonlines.py <dataset_dir> <output.jsonl>
```

---

## 4. 设计说明
- 顺序遍历目录并逐文件 `json.load`。
- 使用 `jsonlines` 库写入，保证每条记录一行。

---

## 5. 关联关系
- 语义上与 `scripts/unpack.py` 相反（一个聚合，一个拆分）。
- 可作为 `scripts/submit.py` 之外的轻量打包工具。

---

## 6. 注意事项
1. 脚本默认目录内文件都可被 JSON 解析。
2. 未按文件名排序；如需稳定顺序可自行改为 `sorted(os.listdir(...))`。
3. 不做字段校验，仅做格式转换。
