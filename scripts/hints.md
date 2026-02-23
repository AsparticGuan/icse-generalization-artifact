# hints.py 说明

## 1. 功能定位
`hints.py` 是样本定位信息抽取模块，负责把 patch 转成：
- 行级定位：`bug_location_lineno`
- 函数级定位：`bug_location_funcname`

这是数据集中最关键的定位提示来源之一。

---

## 2. 核心算法

### 2.1 hunk 有效性过滤 (`is_valid_hunk`)
仅保留“有意义修改”：
- 有删除行；或
- 有非注释新增行。

纯注释新增会被忽略，降低噪声。

### 2.2 行级定位 (`get_line_loc`)
- 对每个有效 hunk，取修改前源码行号区间 `[min, max]`。
- 输出为单文件的多个区间列表。

### 2.3 函数级定位 (`get_funcname_loc`)
1. 把有效 hunk 先转为行区间；
2. 使用 tree-sitter 解析修改前 C++ 源码；
3. 遍历 `function_definition`，判断与区间是否相交；
4. 沿 declarator 递归提取真实函数名；
5. 去掉子串噪声函数名。

---

## 3. 输入与输出

### 输入
- `PatchedFile`（来自 `unidiff`）
- 源代码文本（函数级定位需要）

### 输出
- 行区间列表
- 函数名列表

---

## 4. 依赖与关联

### 直接依赖
- `tree_sitter_cpp`
- `tree_sitter`
- `unidiff`

### 调用方
- `scripts/postfix_extract.py`
- `scripts/dataset_postfix.py`

---

## 5. 注意事项
1. 函数级定位依赖语法解析，遇到异常源码可能提取失败或为空。
2. 行号使用修改前文件坐标，与 patch 语义一致。
3. 子串过滤是启发式策略，不保证在所有复杂声明场景都最优。
