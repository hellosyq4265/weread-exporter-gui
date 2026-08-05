# 微信读书导出工具

支持 Windows GUI 和命令行导出微信读书内容，格式包括 Markdown、EPUB、PDF、TXT，以及 Linux 下的 MOBI。
本项目是基于weread-exporter进行的二次开发，添加了可视化界面以及一些小功能。
（作者是个啥也不会的小白，感谢gpt和deepseek对本项目的大力支持lol）

## Windows GUI

直接运行发布包中的 `WeReadExporterGUI.exe`：

1. 填写微信读书书籍 ID 或书单 ID。
2. 选择导出格式和保存目录。
3. 粘贴 Cookie；没有 Cookie 时可勾选“强制登录（扫码）”。
4. 点击“开始导出”。

GUI 会自动保留未完成任务的缓存，重新启动后发现最近任务并继续导出。章节随机等待可以在界面中关闭或调整。导出完成、失败或没有生成文件时会发送 Windows 通知。

## 命令行

安装依赖：

```bash
python -m pip install -e .
```

导出示例：

```bash
python -m weread_exporter -b BOOK_ID -o md -o epub
```

常用选项：

- `--force-login`：通过扫码登录。
- `--headless`：使用无头浏览器。
- `--single-page`：使用单页阅读模式。
- `--min-load-interval N --max-load-interval N`：设置章节随机等待范围。
- `--no-random-interval`：关闭章节间随机等待。

输出默认写入当前目录的 `output` 文件夹。书籍 ID 是微信读书书籍详情页 URL 末尾的字符串，例如：
`https://weread.qq.com/web/bookDetail/08232ac0720befa90825d88`。

## 实现原理

工具通过 Hook 阅读器页面中的 Canvas 文本及样式，将章节保存为 Markdown，再按需要转换为 EPUB、PDF、TXT 或 MOBI。Cookie 只用于当前导出任务，GUI 和命令行任务结束时都会删除临时 Cookie 文件。

## 免责声明

本工具仅作技术研究之用，请勿用于商业或违法用途。由于使用本工具导致的侵权或其它问题，本工具不承担责任。
