#!/usr/bin/env python3
# Copyright 2025 Yingwei Zheng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""从 patch 中提取行级/函数级 bug 位置提示（hints）。

该模块主要被 postfix_extract.py 与 dataset_postfix.py 调用，用于：
1. 把 patch hunk 映射到源码行区间；
2. 结合 tree-sitter C++ 语法树定位受影响函数；
3. 为数据样本构造 hints.bug_location_lineno / hints.bug_location_funcname。
"""

import tree_sitter_cpp
from tree_sitter import Language, Parser, Tree
from unidiff import PatchedFile, Hunk

# 初始化 C++ 语法解析器（tree-sitter）。
CXX_LANGUAGE = Language(tree_sitter_cpp.language())
cxx_parser = Parser(CXX_LANGUAGE)


def traverse_tree(tree: Tree):
    """深度优先遍历 tree-sitter 语法树节点。"""
    cursor = tree.walk()

    reached_root = False
    while reached_root == False:
        yield cursor.node

        if cursor.goto_first_child():
            continue

        if cursor.goto_next_sibling():
            continue

        retracing = True
        while retracing:
            if not cursor.goto_parent():
                retracing = False
                reached_root = True

            if cursor.goto_next_sibling():
                retracing = False


def intersect_location(ranges, beg, end):
    """判断给定区间 [beg, end] 是否与任一 range 相交。"""
    for b, e in ranges:
        if max(beg, b) <= min(end, e):
            return True
    return False


def is_valid_hunk(hunk: Hunk):
    """判断 hunk 是否包含“有意义的代码修改”。

    规则：
    - 只要有删除行，认为有效；
    - 或存在非注释新增行，也认为有效；
    - 纯注释新增 hunk 视为无效，避免噪声。
    """
    if hunk.removed != 0:
        return True
    for line in hunk:
        if line.is_added and not line.value.strip().startswith("//"):
            return True
    return False


def get_line_loc(patch: PatchedFile):
    """提取单文件 patch 的源码行区间列表。"""
    line_location = []
    for hunk in patch:
        if not is_valid_hunk(hunk):
            continue
        # source_lines() 对应修改前（原文件）的行号范围。
        min_lineno = min(x.source_line_no for x in hunk.source_lines())
        max_lineno = max(x.source_line_no for x in hunk.source_lines())
        line_location.append([min_lineno, max_lineno])
    return line_location


def get_funcname_loc(patch: PatchedFile, source_code: str):
    """提取 patch 命中的函数名列表。

    做法：
    1. 先把有效 hunk 转为行号区间；
    2. 用 tree-sitter 解析修改前源码；
    3. 找出与区间相交的 function_definition；
    4. 归一化 declarator 并去除重复/子串噪声函数名。
    """
    line_location = []
    for hunk in patch:
        if not is_valid_hunk(hunk):
            continue
        min_lineno = min(x.source_line_no for x in hunk.source_lines())
        max_lineno = max(x.source_line_no for x in hunk.source_lines())
        line_location.append([min_lineno, max_lineno])

    # 解析“修改前源码”，用于定位 bug 所在函数。
    tree = cxx_parser.parse(bytes(source_code, "utf-8"))
    modified_funcs = set()
    for node in traverse_tree(tree):
        if node.type == "function_definition" and intersect_location(
            line_location, node.start_point.row, node.end_point.row
        ):
            # 从 function_definition 的 declarator 中向内层递归，提取真实函数名。
            func_name_node = node.children_by_field_name("declarator")[0]
            while True:
                decl = func_name_node.children_by_field_name("declarator")
                if len(decl) == 0:
                    if func_name_node.type == "reference_declarator":
                        func_name_node = func_name_node.child(1)
                        continue
                    break
                func_name_node = decl[0]
            func_name = func_name_node.text.decode("utf-8")
            modified_funcs.add(func_name)

    # 去掉子串关系噪声，例如同时出现 foo 与 ns::foo 时保留更具体项。
    modified_funcs_valid = list()
    for func in modified_funcs:
        substr = False
        for rhs in modified_funcs:
            if func != rhs and func in rhs:
                substr = True
                break
        if not substr:
            modified_funcs_valid.append(func)

    return modified_funcs_valid
