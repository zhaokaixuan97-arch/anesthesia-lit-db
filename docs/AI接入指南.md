# AI 接入指南（MCP）

本库自带一个 MCP（Model Context Protocol）服务端 `mcp/server.py`，让任何支持 MCP 的 AI 客户端都能检索这个文献库。

**零第三方依赖**：只用 Python 标准库 + PyYAML，不用装 `mcp` 之类的包。

## 一、它暴露了什么

| 工具 | 作用 | 典型问法 |
|---|---|---|
| `search_literature` | 按关键词、主题、类型、年份检索 | "库里关于术后谵妄的证据有哪些？" |
| `get_paper` | 取某篇的完整题录与笔记 | "把 2022-li-regional-general-anesthesia 的笔记给我" |
| `list_topics` | 列出主题树与各主题文献数 | "我们库覆盖了哪些方向？哪些还是空白？" |
| `library_stats` | 全库统计 | "库里有多少篇 RCT？" |
| `find_related` | 找主题相近的文献 | "和 RAGA 那篇主题接近的还有哪些？" |

## 二、先自测

```bash
python mcp/server.py --selftest
```

正常会打印统计和几条检索结果。如果这一步报错，先解决依赖问题再往下走。

## 三、配置客户端

MCP 服务端通过 **stdio** 通信，配置里只需要写清 `command` 和脚本路径。

### Claude Desktop

编辑配置文件：

- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "anesthesia-lit-db": {
      "command": "python",
      "args": ["D:\\path\\to\\anesthesia-lit-db\\mcp\\server.py"]
    }
  }
}
```

### Cursor

`.cursor/mcp.json`（项目级）或全局配置，格式同上。

### 其他客户端

只要支持 MCP stdio 传输，配置方式基本一致：`command` 指向 Python，`args` 指向 `mcp/server.py` 的绝对路径。

> 如果 `python` 不在 PATH 里，把 `command` 换成 Python 解释器的绝对路径。

## 四、数据从哪来

服务端启动后会自动扫描 `papers/*/meta.yaml` 和 `notes.md`，并在文件变动后自动重载——**新增文献不需要重启客户端**。

因此只要 `papers/` 目录更新（`git pull` 之后），AI 立刻就能检索到新内容。

## 五、提问技巧

AI 的回答质量取决于库里的笔记质量。建议：

- **笔记写清楚结论和数字**，不要只写"有效/无效"。AI 会引用你写的笔记。
- **标好主题和证据等级**，AI 才能做"这个主题下有哪些高质量证据"这类判断。
- **问的时候限定范围**："只看 2020 年以后的指南"、"只限 RCT"。
- **要求带出处**：让 AI 给出 DOI/PMID，方便你核对。

## 六、局限

- 本库是**文献检索库**，不是临床决策系统。AI 的回答仅供参考，不能替代临床判断。
- 检索基于关键词匹配（中文子串 + 英文词），没有做语义向量检索。库到几千篇后可以考虑加向量检索。
- 库里没有的文献，AI 查不到，也可能"编"。要求它给出 DOI 并核对。
