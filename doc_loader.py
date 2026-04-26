"""
doc_loader.py  —  真实文档 → 带权限标签的 Document 列表

支持格式：PDF、Word(.docx)、Markdown(.md)、Excel(.xlsx)
使用方式：
    from doc_loader import load_documents_from_folder
    docs = load_documents_from_folder("./docs")

依赖安装：
    pip install llama-index pymupdf python-docx openpyxl
"""

import os
from pathlib import Path
from llama_index.core import Document


# ══════════════════════════════════════════════════════════════════════════════
# 1. 文件夹目录约定
#
# 把文件按角色放到对应子文件夹，加载器自动读取文件夹名作为 roles 标签：
#
#   docs/
#   ├── all/              ← 全员可见
#   │   ├── 员工手册.pdf
#   │   └── 考勤制度.docx
#   ├── hr/               ← 仅 HR 可见
#   │   ├── 薪资结构.xlsx
#   │   └── 绩效考核细则.pdf
#   ├── dev/              ← 仅研发可见
#   │   ├── 代码规范.md
#   │   └── 系统架构.pdf
#   └── admin/            ← 仅管理层可见
#       └── Q1经营数据.xlsx
#
# 一个文件需要多角色访问时，在 MULTI_ROLE_FILES 里手动配置。
# ══════════════════════════════════════════════════════════════════════════════

# 特殊配置：某些文件需要多个角色可见（不在单一子文件夹能表达的情况）
MULTI_ROLE_FILES = {
    # "薪资结构.xlsx": ["hr", "admin"],  # 示例：同时给 hr 和 admin
}


# ── 各格式的解析函数 ──────────────────────────────────────────────────────────

def parse_pdf(file_path: Path) -> str:
    """
    用 PyMuPDF (fitz) 解析 PDF，逐页提取文字，保留页码信息。
    对扫描版 PDF 效果差，扫描版建议先用 PaddleOCR 转文字。
    """
    try:
        import fitz  # pip install pymupdf
    except ImportError:
        raise ImportError("请先安装：pip install pymupdf")

    doc = fitz.open(str(file_path))
    pages = []
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if text:  # 跳过空白页
            pages.append(f"[第{page_num}页]\n{text}")
    doc.close()
    return "\n\n".join(pages)


def parse_docx(file_path: Path) -> str:
    """
    用 python-docx 解析 Word 文档。
    保留标题层级（Heading 1/2/3 加 # 前缀），便于后续按标题分片。
    表格内容逐行提取，列之间用 | 分隔。
    """
    try:
        from docx import Document as DocxDocument
    except ImportError:
        raise ImportError("请先安装：pip install python-docx")

    doc = DocxDocument(str(file_path))
    parts = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name
        if "Heading 1" in style:
            parts.append(f"\n# {text}")
        elif "Heading 2" in style:
            parts.append(f"\n## {text}")
        elif "Heading 3" in style:
            parts.append(f"\n### {text}")
        else:
            parts.append(text)

    # 提取表格内容
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append("\n" + "\n".join(rows))

    return "\n".join(parts)


def parse_markdown(file_path: Path) -> str:
    """Markdown 直接读取原文，无需特殊处理"""
    return file_path.read_text(encoding="utf-8")


def parse_excel(file_path: Path) -> str:
    """
    用 openpyxl 解析 Excel。
    每个 Sheet 作为独立段落，表头 + 数据行转为文字描述，
    便于向量检索（向量库不擅长直接检索纯数字表格）。
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError("请先安装：pip install openpyxl")

    wb = openpyxl.load_workbook(str(file_path), data_only=True)
    sheets_text = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # 第一行作为表头
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        lines = [f"【{sheet_name}】"]

        for row in rows[1:]:
            # 跳过全空行
            if all(cell is None for cell in row):
                continue
            # 把每一行转成 "表头: 值" 的文字描述
            desc_parts = []
            for header, val in zip(headers, row):
                if val is not None and str(val).strip():
                    desc_parts.append(f"{header}: {val}")
            if desc_parts:
                lines.append("，".join(desc_parts))

        sheets_text.append("\n".join(lines))

    wb.close()
    return "\n\n".join(sheets_text)


# ── 格式路由 ──────────────────────────────────────────────────────────────────

PARSERS = {
    ".pdf":  parse_pdf,
    ".docx": parse_docx,
    ".doc":  parse_docx,
    ".md":   parse_markdown,
    ".txt":  parse_markdown,   # txt 同 markdown，直接读
    ".xlsx": parse_excel,
    ".xls":  parse_excel,
}


def parse_file(file_path: Path) -> str:
    """根据文件扩展名选择解析器"""
    ext = file_path.suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {list(PARSERS.keys())}")
    return parser(file_path)


# ── 主入口：从文件夹加载 ──────────────────────────────────────────────────────

def load_documents_from_folder(root_dir: str) -> list[Document]:
    """
    扫描 root_dir 下的所有子文件夹，子文件夹名即为角色标签。
    返回带完整 metadata 的 Document 列表，可直接传给 01_ingest.py 使用。

    参数：
        root_dir: 文档根目录路径，如 "./docs"

    返回：
        list[Document]，每个 Document 包含：
            text     : 解析出的文字内容
            metadata : roles / source / filename / dept / doc_type
    """
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {root_dir}")

    documents = []
    skipped = []

    # 遍历子文件夹（每个子文件夹名 = 角色）
    for role_dir in sorted(root.iterdir()):
        if not role_dir.is_dir():
            continue

        folder_role = role_dir.name  # 文件夹名即角色，如 "hr"、"dev"

        for file_path in sorted(role_dir.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.name.startswith("."):  # 跳过隐藏文件
                continue

            ext = file_path.suffix.lower()
            if ext not in PARSERS:
                skipped.append(str(file_path))
                continue

            # 检查是否在多角色配置中
            roles = MULTI_ROLE_FILES.get(file_path.name, [folder_role])

            print(f"  解析: {file_path.relative_to(root)} → roles={roles}")

            try:
                text = parse_file(file_path)
                if not text.strip():
                    print(f"    警告：文件内容为空，跳过")
                    continue

                doc = Document(
                    text=text,
                    metadata={
                        "roles":     roles,
                        "source":    file_path.name,          # 用于引用溯源展示
                        "filename":  str(file_path),          # 完整路径，调试用
                        "dept":      folder_role,             # 归属部门/角色文件夹
                        "doc_type":  ext.lstrip(".").upper(), # PDF / DOCX / MD ...
                    }
                )
                documents.append(doc)

            except Exception as e:
                print(f"    错误：解析失败 {file_path.name} — {e}")

    if skipped:
        print(f"\n跳过 {len(skipped)} 个不支持格式的文件: {skipped}")

    print(f"\n共加载 {len(documents)} 个文档")
    return documents


# ── 单文件加载（手动指定角色）────────────────────────────────────────────────

def load_single_file(file_path: str, roles: list[str], extra_meta: dict = None) -> Document:
    """
    加载单个文件并手动指定角色，适合文件夹结构不固定的场景。

    用法：
        doc = load_single_file(
            "path/to/薪资表.xlsx",
            roles=["hr", "admin"],
            extra_meta={"dept": "人力资源部", "doc_type": "薪资"}
        )
    """
    path = Path(file_path)
    text = parse_file(path)

    metadata = {
        "roles":    roles,
        "source":   path.name,
        "filename": str(path),
        "dept":     roles[0] if roles else "unknown",
        "doc_type": path.suffix.lstrip(".").upper(),
    }
    if extra_meta:
        metadata.update(extra_meta)

    return Document(text=text, metadata=metadata)


# ── 调试入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # 用法一：加载整个文件夹
    #   python doc_loader.py ./docs
    if len(sys.argv) == 2:
        docs = load_documents_from_folder(sys.argv[1])
        for d in docs:
            preview = d.text[:100].replace("\n", " ")
            print(f"\n[{d.metadata['roles']}] {d.metadata['source']}")
            print(f"  字符数: {len(d.text)} | 预览: {preview}...")

    # 用法二：测试单个文件解析
    #   python doc_loader.py ./docs/hr/薪资结构.xlsx hr admin
    elif len(sys.argv) >= 3:
        file_path = sys.argv[1]
        roles = sys.argv[2:]
        doc = load_single_file(file_path, roles)
        print(f"文件: {file_path}")
        print(f"角色: {roles}")
        print(f"字符数: {len(doc.text)}")
        print(f"内容预览:\n{doc.text[:500]}")

    else:
        print("用法:")
        print("  加载文件夹: python doc_loader.py ./docs")
        print("  测试单文件: python doc_loader.py ./docs/hr/薪资.xlsx hr admin")
