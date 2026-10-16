#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《数字见习生》PDF 电子书构建脚本
================================

功能：
  1. 使用 pandoc 将合并版 markdown 转换为 PDF
  2. 使用 xelatex 引擎支持中文
  3. 应用自定义 LaTeX 模板（可选）
  4. 生成带目录、页眉页脚的 PDF

用法：
  python3 build_pdf.py                # 默认：使用默认模板生成 PDF
  python3 build_pdf.py --template template.tex  # 使用自定义 LaTeX 模板
  python3 build_pdf.py --toc          # 生成目录
  python3 build_pdf.py --toc-depth 3  # 目录深度（默认 2）
  python3 build_pdf.py --output file.pdf  # 指定输出文件名
  python3 build_pdf.py --verbose      # 显示详细输出

依赖：
  - python3
  - pandoc（已安装）
  - xelatex（TeX Live 或 MacTeX）
  - 中文字体（如 Noto Sans CJK SC、PingFang SC 等）

安装依赖（macOS）：
  brew install pandoc
  brew install --cask mactex  # 或安装 MacTeX
"""

import argparse
import os
import subprocess
import sys


def find_merged_md():
    """查找合并版 markdown 文件"""
    candidates = [
        'book/输出/《数字见习生》全书稿_合并版.md',
        '输出/《数字见习生》全书稿_合并版.md',
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def build_pdf(input_md, output_pdf, template=None, toc=False, toc_depth=2, verbose=False):
    """
    构建 PDF
    
    Args:
        input_md: 输入 markdown 文件路径
        output_pdf: 输出 PDF 文件路径
        template: LaTeX 模板文件路径（可选）
        toc: 是否生成目录
        toc_depth: 目录深度（默认 2）
        verbose: 是否显示详细输出
    """
    # 构建 pandoc 命令
    cmd = [
        'pandoc',
        input_md,
        '-o', output_pdf,
        '--pdf-engine=xelatex',
        '-V', 'CJKmainfont=Noto Sans CJK SC',
        '-V', 'CJKsansfont=Noto Sans CJK SC',
        '-V', 'CJKmonofont=Noto Sans CJK SC',
        '-V', 'geometry:margin=1in',
        '-V', 'fontsize=12pt',
        '-V', 'linestretch=1.5',
    ]
    
    # 添加模板
    if template and os.path.isfile(template):
        cmd.extend(['--template', template])
    
    # 添加目录
    if toc:
        cmd.append('--toc')
        cmd.extend(['--toc-depth', str(toc_depth)])
    
    # 添加元数据
    cmd.extend([
        '-V', 'title:数字见习生',
        '-V', 'author:jeffery9',
        '-V', 'lang:zh-CN',
    ])
    
    # 显示命令
    if verbose:
        print('执行命令:', ' '.join(cmd))
    
    # 执行 pandoc
    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            timeout=300  # 5 分钟超时
        )
        
        if result.returncode == 0:
            print(f'✓ PDF 生成成功：{output_pdf}')
            if os.path.isfile(output_pdf):
                size_mb = os.path.getsize(output_pdf) / 1024 / 1024
                print(f'  文件大小：{size_mb:.2f} MB')
            return True
        else:
            print(f'✗ PDF 生成失败（返回码：{result.returncode}）', file=sys.stderr)
            if not verbose and result.stderr:
                print('错误信息:', result.stderr, file=sys.stderr)
            return False
    
    except subprocess.TimeoutExpired:
        print('✗ PDF 生成超时（>5 分钟）', file=sys.stderr)
        return False
    except FileNotFoundError:
        print(' 找不到 pandoc 命令，请先安装 pandoc', file=sys.stderr)
        print('  macOS: brew install pandoc', file=sys.stderr)
        return False
    except Exception as e:
        print(f'✗ PDF 生成失败：{e}', file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description='构建《数字见习生》PDF 电子书')
    parser.add_argument('input', nargs='?', help='输入 markdown 文件路径（默认自动查找）')
    parser.add_argument('-o', '--output', help='输出 PDF 文件路径（默认：输出/数字见习生.pdf）')
    parser.add_argument('-t', '--template', help='LaTeX 模板文件路径')
    parser.add_argument('--toc', action='store_true', help='生成目录')
    parser.add_argument('--toc-depth', type=int, default=2, help='目录深度（默认 2）')
    parser.add_argument('-v', '--verbose', action='store_true', help='显示详细输出')
    
    args = parser.parse_args()
    
    # 查找输入文件
    if args.input:
        input_md = args.input
    else:
        input_md = find_merged_md()
    
    if not input_md or not os.path.isfile(input_md):
        print(f'✗ 找不到输入文件：{input_md}', file=sys.stderr)
        print('请先运行 assemble_book.py 生成合并版 markdown', file=sys.stderr)
        sys.exit(1)
    
    print(f'输入文件：{input_md}')
    
    # 确定输出文件路径
    if args.output:
        output_pdf = args.output
    else:
        output_dir = 'book/输出' if os.path.isdir('book/输出') else '输出'
        os.makedirs(output_dir, exist_ok=True)
        output_pdf = os.path.join(output_dir, '数字见习生.pdf')
    
    print(f'输出文件：{output_pdf}')
    
    # 构建 PDF
    success = build_pdf(
        input_md,
        output_pdf,
        template=args.template,
        toc=args.toc,
        toc_depth=args.toc_depth,
        verbose=args.verbose
    )
    
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
