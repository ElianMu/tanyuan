"""Stage 4 real format roundtrips, path safety and PDF visual artifact."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from api import context
from utils import path_utils, converter_utils
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher=patch.object(path_utils,'OUTPUT_DIR',Path(self.tmp.name));patcher.start();self.addCleanup(patcher.stop)
        self.directory=path_utils.get_session_dir('files');self.directory.mkdir()
        context.set_session('files',str(self.directory));self.addCleanup(context.reset_session)

    def test_markdown_exclusive_and_chinese(self):
        name=generate_markdown.invoke({'content':'# 中文药品\n\n正文','filename':'报告'})
        self.assertEqual(Path(name).read_text(),'# 中文药品\n\n正文')
        with self.assertRaises(FileExistsError): generate_markdown.invoke({'content':'overwrite','filename':'报告'})
        self.assertIn('中文药品',read_file_content.invoke({'filename':'报告.md'}))

    def test_no_session_and_path_escape(self):
        for name in ['../escape','/tmp/escape','..\\escape']:
            with self.assertRaises(path_utils.PathOutOfBoundsError): generate_markdown.invoke({'content':'x','filename':name})
        context.reset_session()
        with self.assertRaises(ValueError): read_file_content.invoke({'filename':'x.txt'})

    def test_real_pdf_chinese_tables_and_no_overwrite(self):
        import pymupdf
        content='# 药品销售报告\n\n中文与表格验证。\n\n| 药品 | 数量 |\n| --- | --- |\n| 阿莫西林 | 100 |\n| 布洛芬 | 200 |\n\n## 结论\n\n库存充足。'
        generate_markdown.invoke({'content':content,'filename':'报告.md'})
        result=convert_md_to_pdf.invoke({'md_filename':'报告.md'})
        with pymupdf.open(result) as pdf:
            text=''.join(page.get_text() for page in pdf)
            self.assertIn('阿莫西林',text);self.assertIn('库存充足',text)
            self.assertGreater(len(pdf[0].get_drawings()),0)
        self.assertIn('已存在',convert_md_to_pdf.invoke({'md_filename':'报告.md'}))
        self.assertIn('阿莫西林',read_file_content.invoke({'filename':'报告.pdf'}))

    def test_html_sanitization_no_fetch(self):
        html=converter_utils.md_to_html('<script>alert(1)</script><img src="file:///etc/passwd" onerror="x">[x](javascript:alert(1))')
        for value in ['<script','<img','onerror','javascript:','file:///']: self.assertNotIn(value,html)

    def test_real_docx_with_table_and_encoded_text(self):
        import docx
        document=docx.Document();document.add_paragraph('中文段落')
        document.add_table(rows=1,cols=1).cell(0,0).text='中文表格'
        document.save(self.directory/'sample.docx')
        result=read_file_content.invoke({'filename':'sample.docx'})
        self.assertIn('中文段落',result);self.assertIn('中文表格',result)
        (self.directory/'encoded.txt').write_bytes('中文编码'.encode('gb18030'))
        self.assertEqual(read_file_content.invoke({'filename':'encoded.txt'}),'中文编码')

    def test_real_xlsx_and_xls(self):
        import pandas as pd
        import xlwt
        with pd.ExcelWriter(self.directory/'sample.xlsx') as writer:
            pd.DataFrame({'药品':['阿莫西林'],'数量':[12]}).to_excel(writer,sheet_name='销售',index=False)
            pd.DataFrame({'库存':[20]}).to_excel(writer,sheet_name='库存',index=False)
        workbook=xlwt.Workbook();sheet=workbook.add_sheet('销售');sheet.write(0,0,'数量');sheet.write(1,0,12);workbook.save(str(self.directory/'sample.xls'))
        self.assertIn('工作表：库存',read_file_content.invoke({'filename':'sample.xlsx'}))
        self.assertIn('12',read_file_content.invoke({'filename':'sample.xls'}))

    def test_invalid_missing_and_oversize_files(self):
        self.assertIn('不存在',read_file_content.invoke({'filename':'missing.txt'}))
        (self.directory/'bad.docx').write_bytes(b'not a document')
        self.assertIn('解析失败',read_file_content.invoke({'filename':'bad.docx'}))
        with (self.directory/'huge.txt').open('wb') as stream: stream.truncate(10*1024*1024+1)
        self.assertIn('10 MiB',read_file_content.invoke({'filename':'huge.txt'}))


def visual_artifact():
    import pymupdf
    target=path_utils.OUTPUT_DIR/('stage4-pdf-'+uuid.uuid4().hex)
    target.mkdir(parents=True)
    content='# 探渊 · 药品销售分析\n\n## 数据概览\n\n这是跨平台中文 PDF 验证。报告保留中文、表格与层级标题。\n\n| 药品 | 销售数量 | 销售额 |\n| --- | --- | --- |\n| 阿莫西林胶囊 | 200 | 5000.00 |\n| 布洛芬缓释胶囊 | 1000 | 15000.00 |\n\n## 分析结论\n\n销售数据用于测试，不代表实际业务。\n\n- 检查库存批次和有效期。\n- 对照真实来源，避免编造结论。'
    pdf=converter_utils.convert_markdown_to_pdf(content,target/'中文表格验收.pdf')
    with pymupdf.open(pdf) as document:
        document[0].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3)).save(target/'page-1.png')
    print('VISUAL_ARTIFACT:',target/'page-1.png')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FileTests))
    if result.wasSuccessful(): visual_artifact()
    raise SystemExit(not result.wasSuccessful())
