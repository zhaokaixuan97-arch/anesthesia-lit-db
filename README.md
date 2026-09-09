# 麻醉科文献库

清华大学玉泉医院麻醉科的循证文献知识库。**公开题录 + 人工结构化笔记**，可通过网页检索，也可以直接挂到 AI 客户端里问答。

- 📄 每篇文献一个目录：`meta.yaml`（题录，机器可读）+ `notes.md`（人工笔记）
- 🔍 三种检索方式：网页、命令行、AI（MCP）
- ✅ 提交时自动校验结构 + 扫描患者隐私
- 🧾 只存已发表文献的公开信息，**不含任何患者数据**

---

## 这个库是什么 / 不是什么

| ✅ 可以放 | ❌ 绝对不能放 |
|---|---|
| 已发表文献的题录（标题、作者、期刊、DOI、PMID、摘要） | 患者姓名、住院号、病案号、身份证号、联系方式 |
| 你写的 PICO、结论、局限性、对科室的意义 | 病例细节、手术记录、麻醉记录单、随访表 |
| 学会指南原文要点、证据等级标注 | 病例图片、监护截图、超声图像 |
| 开放获取（OA）文献的 PDF 链接 | 付费文献的 PDF 全文文件 |
| 主题标签、检索笔记 | 未发表数据、在投稿件、审稿意见、基金申请书 |

判断标准只有一句：**「这段内容我能不能直接贴到期刊上或公开网页上？」** 能，就可以进库。

详细说明见 [docs/合规红线.md](docs/合规红线.md)。

---

## 目录结构

```
anesthesia-lit-db/
├── index.html              # 网页检索界面（单文件，无外部依赖）
├── index.json              # 由 build_index.py 生成的检索数据
├── schema/
│   ├── paper.schema.json   # 元数据字段定义
│   └── topics.yaml         # 麻醉专科主题分类树（可扩充）
├── papers/
│   └── 2022-li-regional-general-anesthesia/
│       ├── meta.yaml       # 题录
│       └── notes.md        # 笔记（含自动抓取的摘要 + 人工补充）
├── scripts/
│   ├── fetch_metadata.py   # 从 PubMed/Crossref 抓题录入库
│   ├── validate.py         # 结构校验 + 隐私体检
│   ├── build_index.py      # 生成 index.json / index.sqlite
│   ├── search.py           # 命令行检索
│   └── serve.py            # 本地启动检索网页
├── mcp/
│   └── server.py           # MCP 服务端，供 AI 客户端接入
└── .github/workflows/      # 提交自动校验并重建索引
```

---

## 快速开始

需要 Python 3.10+，依赖只有 `pyyaml` 和 `requests`：

```bash
pip install pyyaml requests
```

### 1. 录入一篇文献

```bash
# 按 DOI 或 PMID 自动抓取题录，并生成目录
python scripts/fetch_metadata.py add 34928310 --topics "术后谵妄与认知功能障碍,老年麻醉" --added-by 张三 --relevance 5
```

然后打开生成的 `papers/<id>/notes.md`，补充 PICO、结论、局限性、对科室的意义。

先看看有哪些候选：

```bash
python scripts/fetch_metadata.py search "postoperative delirium elderly" --limit 10
```

### 2. 校验（提交前必做）

```bash
python scripts/validate.py
```

会检查字段完整性、主题是否合法，并扫描是否误写了患者信息。有严重问题会返回非零退出码。

### 3. 重建索引

```bash
python scripts/build_index.py
```

### 4. 网页检索

```bash
python scripts/serve.py --open          # 本机打开
python scripts/serve.py --lan           # 手机连同一 WiFi 可访问
```

### 5. 命令行检索

```bash
python scripts/search.py "术后谵妄"
python scripts/search.py "opioid" --type RCT --year-from 2020
python scripts/search.py "regional" --full
```

---

## 接入 AI（MCP）

`mcp/server.py` 是一个**零第三方依赖**的 MCP 服务端，暴露 5 个工具：

| 工具 | 用途 |
|---|---|
| `search_literature` | 按关键词/主题/类型/年份检索 |
| `get_paper` | 取某篇的完整题录与笔记 |
| `list_topics` | 列出主题树及各主题文献数（发现空白领域） |
| `library_stats` | 全库统计 |
| `find_related` | 找主题相近的文献 |

自测：

```bash
python mcp/server.py --selftest
```

在支持 MCP 的客户端里加一段配置即可（以 Claude Desktop 为例）：

```json
{
  "mcpServers": {
    "anesthesia-lit-db": {
      "command": "python",
      "args": ["D:\\...\\anesthesia-lit-db\\mcp\\server.py"]
    }
  }
}
```

配置好后就能问：*"我们库里关于术后谵妄的证据有哪些？结论一致吗？"* —— AI 会去查库并带 DOI 回答。

详见 [docs/AI接入指南.md](docs/AI接入指南.md)。

---

## 协作流程

1. 新建分支：`git checkout -b add/2024-xxx`
2. 录入文献、补笔记、跑 `validate.py`
3. 提交 PR，标题写清文献主题
4. 由主题负责人（或主任指定人）审核合并

`topics.yaml` 是公共资产：新增主题请单独提 PR，说明理由。**主题一经启用不要改名**，改名会让历史文献失联。

---

## 常见问题

**Q：GitHub 能放多少东西？**
题录和笔记是纯文本，1000 篇大约 20–50MB，放几万篇都行。**PDF 不要放**：单文件超 100MB 会被直接拒绝，仓库建议保持在 1GB 以内，Git LFS 免费额度只有约 1GB。

**Q：国内访问 GitHub 慢怎么办？**
题录和笔记体积小，正常 clone 没问题。PDF 这类大文件建议放对象存储（如 Cloudflare R2，10GB 免费且出网流量免费）。

**Q：网页能不能直接给主任看？**
`index.html` 需要能读到 `index.json`。用 `python scripts/serve.py` 本机起服务，或部署到 GitHub Pages。**注意**：私有仓库要开 Pages 需要 GitHub Pro/Team；也可以只把题录层公开（题录是公开信息，本身无版权问题），PDF 和敏感内容另外存。

**Q：文献 PDF 版权怎么办？**
付费文献不要提交全文。要留全文就放对象存储，或只保存链接和自己的结构化笔记。OA 文献可以存链接。

---

## 许可

- 代码（`scripts/`、`mcp/`、`index.html`、workflow）：MIT
- 数据（`papers/`、`schema/`）：CC BY 4.0

文献本身的版权归原作者与出版方所有，本库只收录公开题录与自行撰写的笔记。
