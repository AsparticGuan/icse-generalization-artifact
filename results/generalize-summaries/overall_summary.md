## Elements Frequently Missed

- **Vector Types**: Many optimizations were initially written for scalar types only and missed vector variants. This includes both fixed-width and scalable vectors (SVE), requiring matchers to handle splat constants, poison elements, and vector-aware constant folding.
- **Poison and Undef Values**: Transformations frequently failed to account for poison propagation semantics, especially when matching constants like `-1` (all-ones) where poison elements in vectors should be allowed via `m_APIntAllowPoison` variants.
- **Overflow Flags (nsw/nuw)**: Optimizations often missed opportunities to propagate or infer overflow flags, particularly when combining operations like `add` with `min/max` intrinsics or when flags enable predicate conversions.
- **Fast-Math Flags (FMF)**: Floating-point optimizations frequently mishandled flag propagation (`nnan`, `ninf`, `nsz`, `fast`), either dropping flags incorrectly or failing to merge flags from multiple sources.
- **Multi-Use Scenarios**: Transformations were often blocked by single-use constraints when the optimization would still be profitable, or conversely, applied when multi-use would increase instruction count.
- **Commutative Operand Variations**: Pattern matchers frequently missed commuted operand forms, requiring explicit handling via `m_c_`* matchers or operand swapping logic.
- **Dominance and Control Flow**: Optimizations involving PHI nodes often missed dominance-based reasoning, leading to infinite loops on backedges or missed opportunities when values dominate use sites.
- **Edge Cases and Boundary Constants**: Specific constant values like `0`, `-1`, `INT_MIN`, and power-of-two values often required special handling that was initially overlooked.
- **Metadata Preservation**: Debug locations (`DebugLoc`), annotations, and other metadata were frequently lost during instruction replacement or decomposition.
- **Type Mismatches and Extensions**: Transformations involving `zext`, `sext`, `trunc`, and type conversions often missed cases where source and destination types differed.
- **Target-Specific Intrinsics**: SVE, NVPTX, and X86 intrinsics required specialized handling that was often incomplete or missed across intrinsic families.
- **Constant Expressions**: Optimizations sometimes treated `ConstantExpr` differently from instructions, missing folding opportunities or causing crashes.

## Patterns Not Well Handled

### Pattern 1: Overly Specific Pattern Matching

Many optimizations were written to match exact IR patterns with hardcoded constants, specific operand orders, or narrow type constraints. The generalization strategy consistently involved:

- Replacing literal constants with semantic matchers (`m_Power2`, `m_SignMask`, `m_NonNegative`)
- Using commutative matchers (`m_c_Add`, `m_c_ICmp`) to handle operand permutations
- Parameterizing type constraints rather than hardcoding bit widths
- Extracting core mathematical/algebraic properties rather than matching surface syntax

### Pattern 2: Overly Restrictive Preconditions

Optimizations frequently included unnecessary guards that blocked valid transformations:

- Type restrictions (scalar-only, specific bit widths) that weren't semantically required
- Single-use constraints that could be relaxed with profitability analysis
- Declaration-specific guards that prevented optimizing calls to external functions
- Metadata-specific guards (like `invariant.load`) that excluded more general safe cases

### Pattern 3: Incomplete Intrinsic Recognition

Transformations involving intrinsics often missed:

- Multiple intrinsic variants within a family (e.g., all `isspacep.`* intrinsics)
- Both signed and unsigned comparison intrinsics (`scmp`/`ucmp`)
- Intrinsic-to-manual-code equivalence patterns (e.g., `uadd.sat` vs select-based saturation)
- Flag propagation when creating or replacing intrinsics

### Pattern 4: Comparison Predicate Handling

Optimizations involving comparisons frequently missed:

- Predicate equivalence classes (e.g., `ULT` vs `ULE` with adjusted constants)
- Signed-to-unsigned predicate conversion when operands have known signs
- Predicate swapping when operands are commuted
- The `samesign` attribute and its implications for predicate canonicalization

### Pattern 5: Control Flow and PHI Node Transformations

Transformations involving PHI nodes often failed to handle:

- Loop backedges causing infinite combine loops
- Multiple predecessors with different source values
- Moving operations into predecessor blocks safely
- Critical edges and dominance relationships

### Pattern 6: Bitwise and Shift Operation Idioms

Complex bit manipulation patterns were frequently missed:

- Bit testing via sign-bit shifting (`icmp slt (shl X, (sub bw-1, Y)), 0`)
- Mask decomposition for comparisons (`(X & Mask) == C` patterns)
- Distributivity of operations over bitwise operations (`add` over `min/max`)
- `disjoint` flag enabling xor distribution over or

### Pattern 7: Select Instruction Folding

Select-based patterns often missed:

- Folding operations into select arms when one arm simplifies
- Recognizing three-way comparison idioms (`scmp`/`ucmp` patterns)
- Handling vector conditions in select-shuffle folding
- Poison safety when condition could be poison

### Pattern 8: Memory and Pointer Operations

Transformations involving memory operations frequently missed:

- Address space semantics and pointer validity
- Null pointer undefined behavior across address spaces
- Load sinking past calls with proper alias analysis
- GEP canonicalization and constant offset folding

