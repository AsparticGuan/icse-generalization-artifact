# 为什么这些 patch 不能通过 dev_tests：根因分析

基于 `retest-fixes-deepseek-v3.2-cot-iter4-orig-single-nofilecheck` 的 cost 分析，以及各 issue 的**上游 fix**与**生成 patch**的对比，归纳 patch 在 dev_tests 上失败的原因。

---

## 一、按 issue 的根因分类

### 1. 标量/向量或类型未覆盖

| Issue | Patch 做的优化 | 为何在 dev_tests 上 cost 没过 |
|-------|----------------|-------------------------------|
| **58523** | `C - ((C2 - X) & Mask) → (X & Mask) + ...` 在 visitSub | 实现用 `m_APInt(ConstC)` / `m_APInt(MaskVal)` 等只匹配**标量常数**。dev_tests 里如 `sub_to_and_vector1` 是 **`<2 x i8>` 向量**，常量是 vector splat，match 不到，fold 不触发，cur=src。另外 `sub_to_and_nuw` 等标量 case 上游有更复杂的常数条件（C2&C3、nuw/neg_pow2），生成 patch 条件不同，也可能不触发。 |

**结论**：pattern 按标量写的，**向量/非标量未支持**；部分标量 case 与上游条件不一致。

---

### 2. 谓词或条件过窄（只处理了部分 case）

| Issue | Patch 做的优化 | 为何在 dev_tests 上 cost 没过 |
|-------|----------------|-------------------------------|
| **85250** | visitFCmpInst：`fcmp pred (select (fcmp CondPred X, 0), X, -X), 0 → fcmp pred X, 0` | 生成 patch **只允许** select 的条件是四种符号判断：OGE/OLT/OLE/OGT + 零。上游 fix 是「**任意** cond」：`match(LHSI, m_Select(m_Value(), m_Value(X), m_FNeg(m_Deferred(X))))`，不限制 cond。dev_tests 里大量是 **fcmp one/oeq/ueq/une** 等（abs 后与 0 比较），或 **向量** fcmp，当前实现都不匹配，所以 fold 不触发，cur=expect=src 或 cur>expect。 |
| **69803** | visitICmpInst：`(~A ^ B) pred ~A → (A^B) NewPred A` | 生成 patch 里 **只对 SLT/SGT 做了 switch**，其他 predicate 走 default 设成 BAD_ICMP_PREDICATE，不 fold。上游用 **getSwappedPredicate(Pred)** 统一处理 SLT/SGT/SLE/SGE/ULT/UGT/ULE/UGE/EQ/NE。dev_tests 里 test_sle_xor、test_sge_xor、test_ult_xor 等是 **SLE/SGE/ULT/UGE** 等，patch 完全不处理，所以 cost 没过。 |

**结论**：**谓词/条件覆盖过窄**，只实现了 issue 里那一两种 case，没推广到上游的完整 predicate 集。

---

### 3. 生成 patch 与 dev_tests 期望的优化不是同一类

这类里：**patch 只做了一种具体优化**，而 dev_tests 里每个用例期望的是**另一种**优化（或根本不期望被这条 fold 动到）。下面先直观说明什么叫「不是同一类」，再写出各 patch 的 pattern 和 dev_tests 的对照。

---

#### 3.0 为什么叫「不是同一类」——直观解释

**dev_tests 是怎么来的？**  
dev_tests 通常来自 LLVM 的既有测试（如 `and-or-icmps.ll`、`logical-select.ll` 等），这些测试是为了覆盖**该文件/该 pass 里已有的多种优化**写的，而不是专门为「你这个 issue 新加的那一条 fold」写的。

所以会出现这种情况：

- **Patch**：只加了「一种」具体的 IR 变换（例如：把 `(A&&B)&&C` 改成 `A&&(B&&C)`）。
- **Dev_tests**：里边的用例在测的是**别的**变换——例如「两个 icmp 合并成一个」「select 改成 and」「范围矛盾直接 fold 成 false」等。

也就是说：**patch 认的 IR 形状**和**每个 test 里的 IR 形状**往往对不上；就算对上了，**test 期望的优化结果**也未必是 patch 做出来的那种。所以要么 patch 根本不会在这些用例上触发，要么触发了但 test 期望的是另一种结果，cost 就对不上。这就是「不是同一类」的意思：**不是 patch 写错了，而是 patch 在做的「那一类」优化，和 dev_tests 在测的「那一类」优化是两回事。**

下面用一句话概括每个 issue，再在 3.1 里写清 patch 的 pattern。

| Issue | Patch 做的是「哪一类」事 | Dev_tests 在测的是「哪一类」事 | 为何对不上 |
|-------|--------------------------|----------------------------------|------------|
| **76623** | 只做「三层 select 换结合顺序」：`(A&&B)&&C` → `A&&(B&&C)` | 测的是 **icmp 合并、select↔and、逻辑 or 化简、range 矛盾** 等，IR 多是「icmp + select」而不是「三层纯 select」 | 用例的 IR 形状跟 patch 的 match 对不上，patch 基本不会触发 |
| **60167** | 在 InstructionSimplify 里做一种 **select 吸收**：`(X&&Y)\|\|Y`→Y 等 4 种形式 | 测的有 **向量**、**操作数顺序不同** 的吸收、以及 **fcmp+select** 等；有的在 InstCombine 里由别的 fold 做 | patch 只认标量、固定顺序；向量/别的形式不触发；pass 顺序也可能导致 Simplify 已先做过 |
| **152948** | 在 range 合并里 **look through and with mask**，方便把「V&Mask」的 icmp 合并 | 测的有 **non-pow2 要保持不合并**、**select→and**、**eq→ne 改写** 等，和「两个 icmp 都能 look through and 再合并」不是同一条 fold | 很多用例要么不该被这条动，要么期望的是 select→and 等别的变换，不是「icmp range 合并」 |
| **73622** | 在 foldAddToAshr 里 **多认一种 pattern**：`icmp eq (X&Mask), Mask` 也能 fold 成 ashr | 测的里有很多 **负例**（wrong_constant / wrong_mask / wrong_cast），期望就是 **不要优化**，保持 cur=src | patch 在这些负例上正确没动；但 cost 判定要求 cur&lt;src，负例上 cur=src 就判成「没过」——test 期望的是「不优化」这一类结果 |

**总结**：「不是同一类」= **patch 实现的是 A 类优化，dev_tests 里大量用例在验证 B、C、D 类优化（或「此处不要优化」）**，所以用「是否通过 dev_tests」来评判「patch 对不对」会失真；patch 可能在本 issue 的单测上是对的，只是和这批 dev_tests 的考察点不一致。

---

#### 3.1 各 issue 的 patch pattern 详述

**76623（三层 select 结合律）**

| 项目 | 内容 |
|------|------|
| **所在位置** | `InstCombineSelect.cpp` → `foldSelectOfBools` |
| **匹配条件** | `CondVal` = `select(A, B, false)` 且 `FalseVal` = `false`（即外层 select 的 false 边为 0）。不约束 `TrueVal`（即 C）。 |
| **IR 形式（before）** | `select (select i1 A, i1 B, i1 false), i1 C, i1 false` |
| **IR 形式（after）** | `select i1 A, (select i1 B, i1 C, i1 false), i1 false` |
| **逻辑等价** | `(A && B) && C` → `A && (B && C)`（仅改变结合顺序，不化简） |
| **Match 代码** | `match(CondVal, m_Select(m_Value(A), m_Value(B), m_Zero())) && match(FalseVal, m_Zero())` |

---

**60167（select 吸收律，InstructionSimplify）**

| 项目 | 内容 |
|------|------|
| **所在位置** | `InstructionSimplify.cpp` → `simplifySelectInst` |
| **匹配与结果** | 共 4 条，均为「外层 select 的 Cond 是内层 select，且 TrueVal/FalseVal 为 0 或 1」时直接返回内层某操作数 Y： |
| **Pattern 1** | `Cond` = `select(X, Y, 0)` 且 `TrueVal` = 1 → 返回 **Y**。即 `(X && Y) \|\| Y` → Y（与 false 结合的一侧是 Y）。 |
| **Pattern 2** | `Cond` = `select(X, 1, Y)` 且 `TrueVal` = 0 → 返回 **Y**。即 `(X \|\| Y) && Y` 的另一种编码。 |
| **Pattern 3** | `Cond` = `select(X, 0, Y)` 且 `FalseVal` = 1 → 返回 **Y**。 |
| **Pattern 4** | `Cond` = `select(X, Y, 1)` 且 `FalseVal` = 0 → 返回 **Y**。 |
| **Match 要点** | 使用 `m_One()` / `m_ZeroInt()` 匹配 **标量** 0/1；内层 select 形式固定为 `select(X, ?, ?)` 且另一臂为 0 或 1。**不**匹配向量、也不匹配 Cond 为 fcmp 等形式。 |

---

**152948（foldAndOrOfICmpsUsingRanges 里 look through and with mask）**

| 项目 | 内容 |
|------|------|
| **所在位置** | `InstCombineAndOrXor.cpp` → `foldAndOrOfICmpsUsingRanges` |
| **匹配条件** | 在已有 V1、V2（来自两个 icmp 的操作数，可能已 strip 过 add）之后：若 `V1` = `AndV1 & Mask1`（常量 mask），则用 `AndV1` 替代 V1；同理若 `V2` = `AndV2 & Mask2`，则用 `AndV2` 替代 V2。要求替代后 `V1 == V2` 才继续。 |
| **IR 形式（before）** | 例如 `(icmp eq (V & M1), C1) and (icmp eq (V & M2), C2)`，其中 V 同变量、M1/M2 为常量。 |
| **Range 计算** | 若某侧存在 Mask，则在该侧的 ConstantRange 上做：`RangeSize = -*Mask`，`CR = [getLower(), getLower() + RangeSize]`（即把 `(V & Mask) == C` 视为一个连续 range）。**未**区分 mask 是否为 pow2；对 non-pow2 mask 的 range 语义可能与上游/测试预期不一致。 |
| **变换** | 与原有逻辑一致：若两个 range 可合并，则生成一个新的 icmp（如 `add + icmp ult`）。patch 只增加「look through and」和「有 mask 时的 range 计算」，不单独做 select→and 或 eq→ne 等。 |

---

**73622（foldAddToAshr 增加 icmp eq + ExpectedMask 的 pattern）**

| 项目 | 内容 |
|------|------|
| **所在位置** | `InstCombineAddSub.cpp` → `foldAddToAshr` |
| **整体形式** | `Add` = `(X sdiv DivC) + sext(icmp(Pred, (X & MaskC), CmpC))`，其中 DivC 为 2 的幂且非最小有符号数。`MaskC` 必须等于 `ExpectedMask = SMin \| (DivC - 1)`。 |
| **Pattern 1（原有）** | `Pred` = `ICMP_UGT`，`CmpC` = `SMin`（符号位掩码）。即 `(X/DivC) + sext((X & ExpectedMask) >u SMin)` → `ashr X, log2(DivC)`。 |
| **Pattern 2（新增）** | `Pred` = `ICMP_EQ`，`CmpC` = `ExpectedMask`。即 `(X/DivC) + sext((X & ExpectedMask) == ExpectedMask)` → `ashr X, log2(DivC)`。 |
| **约束** | 第二操作数必须是 `sext(icmp(...))`，**zext** 不匹配；mask 必须严格等于 `ExpectedMask`，否则不触发。负例（wrong_constant / wrong_mask / wrong_cast）期望**不**优化，patch 正确不触发，但 cost 判定要求 cur < src，负例上 cur=src 导致判为不过。 |

---

#### 76623（patch：三层 select 结合律）

| dev_test 名称 | 实际 IR pattern（source） | 期望的优化结果（expect） | 与 patch 的关系 |
|---------------|---------------------------|---------------------------|-----------------|
| **logical_and_icmps1** / **logical_and_icmps_vec1** | `icmp sgt a, -1` + `icmp slt a, 10086` + 两层 `select(other_cond, cmp1, false)` / `select(logical_and, cmp2, false)` | 两个 icmp **合并成一个 range 比较**：`icmp ult a, 10086` + 一层 select | 期望的是 **icmp 合并（range 推理）**，不是 select 结合律；patch 不碰 icmp，故不触发。 |
| **reduce_bitwise_and1** | `select(and1, cmp, false)`，其中 and1 = or(a, cmp1) | 变为 **and(and1, cmp)**（即 `select` 改写为 **and**） | 期望的是 **select → and** 的 canonicalization，不是三层 select 重组；patch 只做 select 结合律，不触发。 |
| **reduce_logical_or2** | `xor(c,b)` + `select(a, true, xor)` + `select(and1, true, b)` | 变为 **select(a, true, or(c,b))**（两层 select 合并成一层 or + select） | 期望的是 **逻辑 or 化简**（xor 视情况变 or），不是 (A&&B)&&C 的结合律；patch 不触发。 |
| **reduce_logical_and1** / **reduce_logical_and3** | `select(a, cmp1, false)` + `select(and1, cmp, false)` | 内层两 icmp 先 **and** 起来，再外层 **select(a, and(cmp1,cmp), false)** | 期望的是 **内层 and 先合并、再 select**，与「三层 select 换结合顺序」不同；patch 不触发。 |
| **icmp_slt_0_or_icmp_add_1_sge_100_i32_fail** | `lshr x, 31` + `zext(icmp sge X1, 100)` + `or` | 变为 **icmp slt x, 0** + **icmp sgt X1, 99** + **or i1** + zext（形式略变，cost 同） | 期望的是 **lshr+zext+or 的改写**，与 select 结合律无关；patch 不触发。 |
| **logical_and_icmps2** | `icmp slt a, -1` + `icmp eq a, 10086` + 两层 select | **ret i1 false**（两个 range 相交为空） | 期望的是 **range 矛盾折叠**，不是 select 结合律；patch 不触发。 |
| **logical_and_icmps_fail1** / **reduce_*_fail*** | 与上面类似，但常量或结构略不同，或为「不应优化」的负例 | 期望**保持不优化**或仅小幅改写 | 名称带 fail 的用例本身就不期望被激进 fold；部分会 timeout。 |

**小结**：76623 的 dev_tests 来自 and-or-icmps.ll / logical-select.ll，期望的是 **icmp 合并（range）、select↔and/or 互化、逻辑 and/or 化简、range 矛盾** 等，**没有**「三层 select 仅换结合顺序」这一条；patch 只做后者，所以对上述用例**根本不会触发**，cur=src 或 cur>expect。

---

#### 60167（patch：InstructionSimplify 里 select 吸收 (X&&Y)||Y→Y）

| dev_test 名称 | 实际 IR pattern（source） | 期望的优化结果（expect） | 与 patch 的关系 |
|---------------|---------------------------|---------------------------|-----------------|
| **land_lor_left1** / **land_lor_left2** | `select(A, B, false)` + `select(c, true, A)` | **ret A**（吸收律：与 patch 一致） | 若 retest 只跑 **instcombine**，Simplify 可能先已跑过；或 match 条件（Cond 必须是 select(X,Y,0) 且 TrueVal=1）与具体 IR 顺序不完全一致，导致不触发。 |
| **or_logic_vector** | `and(x, y)` + `select(x, true, and)` | **ret x**（x \|\| (x&&y) = x） | 这里是 **向量** `<2 x i1>`，且是 **select(x, true, and)**，与 patch 的「Cond=select(..., false), TrueVal=1」形式不同；期望的是 **逻辑 or 吸收**，可能在 InstCombine 里用别的 fold 做。 |
| **or_and_common_op_commute1** / **commute2** | `select(y, x, 0)` + `select(a, true, y)`（或 x/y 交换） | **ret y**（Y \|\| (X&&Y) = Y） | 与 patch 的 (X&&Y)\|\|Y→Y 是同一类吸收，但 **操作数顺序**（Cond 是 select(y,x,0) 时 TrueVal=1 在第二操作数）可能和 patch 的 match 顺序要求不一致；且部分在 **instsimplify** 的 test 里，retest 若统一用 instcombine，pass 顺序不同。 |
| **or_and_not_common_op** | `select(x, y, false)` + `select(a, true, z)`，**没有公共操作数** | 期望**保持**（不化简） | 正确行为就是不变；cost 没过可能是 expect 写成了「保持」而 cur 也保持，但 cost 判定 cur<src 不成立。 |
| **or_cmps** | **fcmp uno** + **select(cmp1, true, cmp2)** | 期望**保持**（两个 fcmp + select） | **浮点 cmp + select**，不是 bool select 吸收；patch 不涉及 fcmp，不触发。 |

**小结**：60167 的 dev_tests 混合了 **InstSimplify** 的 select 吸收（land_lor、or_and_common_op）和 **InstCombine/InstSimplify** 的 or 吸收（or_logic_vector 等）；patch 在 Simplify 里只做一种吸收，且 match 较死板（操作数顺序、标量）。retest 统一 **instcombine** 时，Simplify 的 case 可能已在前面 pass 做过，而 or_logic_vector 等期望的是**别的** or 化简（或向量形式），与当前 patch **不是同一条 fold**，故 cur>expect 或 cur=src。

---

#### 152948（patch：foldAndOrOfICmpsUsingRanges 里 look through and with mask）

| dev_test 名称 | 实际 IR pattern（source） | 期望的优化结果（expect） | 与 patch 的关系 |
|---------------|---------------------------|---------------------------|-----------------|
| **neg_or_icmp_eq_and_non_pow2_mask** | `icmp eq x, 127` + `and x, -33` + `icmp eq and, 128`，**or**；mask **-33 非 pow2** | 期望**保持**（不合并成单一 icmp） | patch 的「look through and」+ range 用 `RangeSize = -*Mask`，对 **non-pow2 mask** 的 range 语义不对或故意不 fold；expect 就是保持，cur=src，但 cost 判定可能 cur<src 不成立。 |
| **neg_and_icmp_ne_and_non_pow2_icmp** | `icmp ne` + `and x, 非 pow2` + `icmp ne`，**and** | 期望**保持** | 同上，non-pow2 不合并；patch 不触发或不应触发。 |
| **neg_and_icmp_ne_and_pow2_disjoint** | `icmp ne x, 126` + `and x, -32` + `icmp ne and, 128`，**and** | 期望**保持**（两个 range 不相交） | 期望的是「disjoint 则不合并」，不是 look through and；patch 可能只做了 look through，没做合并，或合并条件不包含 disjoint。 |
| **neg_select_icmp_eq_and_pow2** | **select**(sgt x, 127, **icmp eq and, 128**, false) | 变为 **and**(sgt, icmp eq)（**select → and**） | 期望的是 **select 转 and**，不是 icmp range 合并；patch 在 foldAndOrOfICmpsUsingRanges 里，不负责 select→and。 |
| **and_icmp_eq_with_binary_range_operands** | `icmp eq x, 1` + `icmp eq y, 1`，**and**；带 **range** 属性 | 变为 **icmp ne x, 0** + **icmp ne y, 0** + and | 期望的是 **range 信息下的 eq→ne** 改写，与「and 与 mask」无关；patch 不触发。 |
| **and_icmp_eq_with_binary_range_operands** 等 | 若干 **pow2** 的 and+icmp 合并成 **add + icmp ult** | 单一 **add + icmp** | patch 的「look through and」是为 range 合并**铺路**，若后续 range 合并仍要求 pow2、或 V1/V2 必须同变量，这些用例可能仍不满足，导致不触发。 |

**小结**：152948 的 dev_tests 里：**non_pow2** 用例期望**不**做 range 合并（或语义不同）；**pow2** 的期望合并成 add+icmp；还有 **select→and**、**binary range 的 eq→ne** 等，与「look through and mask」**不是同一条 fold**，或需要后续 range 逻辑配合；patch 只做 look through + 部分 range，很多 case 不触发或不应触发，cur=exp=src 或 cur>expect。

---

#### 73622（patch：foldAddToAshr 增加 icmp eq + ExpectedMask 的 pattern）

| dev_test 名称 | 实际 IR pattern（source） | 期望的优化结果（expect） | 与 patch 的关系 |
|---------------|---------------------------|---------------------------|-----------------|
| **floor_sdiv_by_2** | `sdiv x, 2` + `and x, -127` + **icmp eq and, -127** + **sext** + add | **ashr x, 1**（正确 floor sdiv by 2） | 正例：patch 新增的 **pattern2（icmp eq + ExpectedMask）** 就是为这种形式；若 match 成功则 cur=expect。 |
| **floor_sdiv_by_2_wrong_constant** | `sdiv x, **4**` + `and x, **-125**` + icmp eq and, -125 + sext + add | 期望**完全不优化**（除数/常数错，不能 fold 成 ashr） | **负例**：expect 与 source 相同；patch **不应**触发。cur=src=exp=15，但 cost 判定要求 cur**<**src，故判 cost 没过。 |
| **floor_sdiv_by_2_wrong_mask** | `sdiv x, 2` + `and x, **127**`（正数 mask，非 SMin\|(DivC-1)）+ icmp eq + sext + add | 期望**完全不优化** | **负例**：mask 不对，不应 fold；cur=src=exp，同上 cost 判定不过。 |
| **floor_sdiv_by_2_wrong_cast** | `sdiv x, 2` + and(-127) + icmp eq + **zext**（不是 sext）+ add | 期望**完全不优化** | **负例**：foldAddToAshr 只认 **sext**；cur=src=exp，cost 判定不过。 |

**小结**：73622 的 dev_tests 里 **wrong_*** 都是**负例**，期望**保持原样**；patch 正确地在这些 case 上**没有**触发。但 cost 判定是「cur < src 且 cur <= expect」：对负例 cur=src=expect，**cur < src 不成立**，所以被算成 cost 没过。正例 floor_sdiv_by_2 若被 patch 的 pattern2 命中则会过；失败记录里主要是 wrong_*，即「dev_tests 期望的本来就是不优化」的 pattern，与「patch 做的是另一种正例 pattern」不是同一类。

---

**结论**：**patch 只做了一种具体优化**，dev_tests 里要么期望的是**别的** fold（icmp 合并、select↔and、or 吸收、range 合并、select→and、负例不优化等），要么 pass/顺序不一致，自然多数 case 不会触发或 cost 不达标。

---

### 4. 实现细节与上游不一致（OneUse、输出形式、匹配形式）

| Issue | Patch 做的优化 | 为何在 dev_tests 上 cost 没过 |
|-------|----------------|-------------------------------|
| **157524** | visitSub：`smin(X+1,Y) - smin(X,Y) → select(X<Y, 1, 0)`（及对称的 -1/0） | 生成 patch 用了 **m_OneUse(m_c_SMin(...))**，上游是 `(Op0->hasOneUse() \|\| Op1->hasOneUse())`，且上游用 **m_NSWAddLike**、最后生成 **zext(icmp)**。dev_tests 里若 smin 有多用、或 add 形式不是简单 m_Add，就不会匹配；且若测试期望 **zext** 而 patch 输出 **select**，cost/IR 也可能对不上。 |
| **142593** | visitTrunc：`trunc nuw (lshr X, BW-1) → icmp slt X, 0` | 要求 **Trunc.hasNoUnsignedWrap()**；上游是 `match(Src, m_Shr(..., m_SpecificInt(SrcWidth-1)))` 且**不**要求 nuw，并同时支持 ashr/lshr。dev_tests（test2/test4/test5）的 IR 可能没有 nuw、或是 ashr、或后续还要别的 fold 才能得到期望的「ret i1 false」，**单靠这条 trunc 不够**。 |

**结论**：**OneUse/nuw/ashr 等条件更严**，或**输出形式与上游/测试预期不一致**，导致不触发或 cost 不达标。

---

### 5. 多步优化 / 测试期望整段简化

| Issue | 说明 |
|-------|------|
| **142593** | issue 里期望的是整段简化成「store + ret i1 false」，需要 **trunc→icmp** 之后，再配合 select/xor 等其它 fold，单条 trunc 优化不足以让 cost 达到 expect。 |
| **133344** | 唯一失败用例 trunc_equality_either：cur=exp=src=4，**没有任何优化发生**，可能是 pattern 与当前 IR 不完全一致（如 trunc 来源、相等比较形式），或需要前序 pass 先改写 IR，patch 才可能命中。 |

**结论**：测试期望的是**多步/整段**优化，单条 patch 只做一步，**不足以覆盖**。

---

### 6. 运行错误（timeout 等）

| Issue | 说明 |
|-------|------|
| **76623** | logical_and_icmps_fail1、reduce_logical_or_fail1、reduce_logical_and_fail2 等 **opt 超时**，无 cost 可看，属环境/复杂度问题。 |
| **98800** | select_of_symmetric_selects_vector3 **opt 超时**。 |

---

## 二、根因汇总表

| 根因类型 | 涉及 Issue | 建议方向 |
|----------|------------|----------|
| **标量/向量或类型未覆盖** | 58523, 85250(向量) | 用可匹配 vector 的 match（如 splat 常量）、或显式处理向量。 |
| **谓词/条件过窄** | 85250, 69803 | 按上游补全 predicate（如 getSwappedPredicate）、或放宽 select 条件（任意 cond）。 |
| **patch 与 dev_tests 期望的优化不是同一类** | 76623, 60167, 152948, 73622 | 要么扩展 patch 覆盖更多 fold，要么接受「只保证 issue 单测」、dev_tests 需单独筛选。 |
| **OneUse/输出形式/匹配过严** | 157524, 142593 | 放宽 OneUse/nuw、与上游一致用 zext、或同时支持 ashr/lshr。 |
| **多步/整段优化** | 142593, 133344 | 增加后续 fold 或接受单步改进；或标注这些 test 依赖多 pass。 |
| **运行错误** | 76623, 98800 | 提高 timeout、或缩小 test、或跳过已知重用例。 |

---

## 三、一句话结论

- **58523 / 85250 / 69803**：实现**只覆盖了标量或少数谓词**，dev_tests 里大量是**向量或其它 predicate**，pattern 没命中，cost 自然不过。
- **76623 / 60167 / 152948 / 73622**：patch 只做了**一种**优化，dev_tests 测的是**多种/其它**优化，**不是同一条 fold**，所以多数 case 不会触发或达不到期望 cost。
- **157524 / 142593**：**OneUse/nuw/zext 等实现细节**与上游或测试预期不一致，或需要**多步优化**才能达到 expect。
- **133344**：单条用例无优化，多为 **pattern/前序 IR 不匹配** 或 **多步依赖**。
- **76623 / 98800** 部分用例：**timeout**，属环境/规模问题。

整体上：**“patch 为何不能通过这些 test case”** = 要么 **pattern/类型/谓词覆盖不足**（向量、多谓词、多 fold），要么 **实现比上游更严或输出形式不同**，要么 **测试期望的是别的优化或多步结果**；再叠加少量 **timeout**。
