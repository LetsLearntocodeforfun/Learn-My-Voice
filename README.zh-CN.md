# Learn-My-Voice

[English](README.md) | **简体中文**

一个在自己电脑上运行的中英双语脚本工作台，给小红书和其他短视频创作者用。
它从你以前亲手写的脚本里学习你平时怎么说话，照着你的口吻写新脚本、帮你检查，再把结果交给剪映（国际版叫 CapCut）去剪辑。

除了 Python 以外不需要安装任何东西。在 macOS 上，Python 包含在苹果免费的「命令行工具」里；如果还没装，启动文件会提示你安装。

![脚本编辑页和实时检查](docs/images/script-zh.png)

*截图里用的是虚构的示例数据。*

## 目前的状态

这是早期版本，使用前请先看这一段。

| 部分 | 状态 |
| :--- | :--- |
| 脚本编辑、检查、提词器、素材库、导出 | 可以用。有自动化测试，也在真实浏览器里跑过（macOS 上的无界面 Chrome）。 |
| AI 功能（写初稿、学习声音、选题建议） | 测试里用的是模拟的模型回复。真实模型写出来的质量还没有评估过。 |
| 热点关键词 | 粘贴、排序和关键词检查可以用，有自动化测试，也在无界面 Chrome 里跑过。排序的权重是凭经验定的，**还没有**用真实结果校准过。让能联网的 AI 查热词这一步，只用模拟回复测试过。 |
| 剪映 / CapCut | 导出的是可以导入的标准文件。维护者**还没有**在剪映或 CapCut 里实际验证过导入。目前不会直接生成剪映草稿。 |
| MCP 服务 | 在协议层面测试过（标准输入输出上的 JSON-RPC）。**还没有**和 ChatGPT、Codex 或 Claude Desktop 实际连通验证。 |
| 系统 | 在 macOS、Python 3.9 上开发。Windows 和 Linux 没有测试过。 |

## 快速开始

需要 Python 3.9 或更新版本，不用安装任何依赖。

```bash
git clone https://github.com/LetsLearntocodeforfun/Learn-My-Voice.git
cd Learn-My-Voice
python3 -m studio.server
```

工作台会在浏览器里打开，地址是 `http://127.0.0.1:8765`。

在 macOS 上也可以直接双击 `启动工作台.command`（或 `Start Learn My Voice.command`）。
如果第一次打不开，请右键点这个文件，选择「打开」。

界面默认是中文。点左下角的 **English** 可以切换成英文。

## 头十分钟怎么用

1. **设置**：选择 AI 的来源（见下一节）。默认的方式不需要密钥。
2. **素材库**：上传或粘贴你以前亲手写的脚本（`.txt`、`.md`、`.docx`），建议 5 篇以上。有数据的话填上播放、点赞、收藏、评论；商单请勾选「商单」。
3. **我的声音**：点「从素材库学习我的声音」，然后填上你真实的语速。
4. **热点**（可选）：把你在小红书看到的热词贴进来，让它结合你自己的数据排个先后。
5. **选题建议**：让它帮你想选题，挑一个，直接写初稿。
6. **我的脚本**：修改初稿，用提词器念，再导出剪映素材包。

在完成第 3 步之前，工作台用的是自带的*示例*声音档案（一个虚构的博主），页面上会有提示。

## AI 从哪里来

| 方式 | 需要什么 | 你的文字会发到哪里 |
| :--- | :--- | :--- |
| **ChatGPT 订阅（复制粘贴）**，默认 | 一个 ChatGPT 账号，或任何聊天类 AI。不需要 API 密钥。 | 你粘贴进去的那个聊天窗口 |
| **MCP** | 支持 MCP 的 AI 应用（见下文） | 那个应用的服务商 |
| **Google Gemini** 或 **OpenAI** 的 API | API 密钥 | Google 或 OpenAI |
| **本地模型** | 在同一台电脑上运行的 Ollama 或 LM Studio | 不会离开这台电脑 |

### 用订阅复制粘贴

ChatGPT 订阅不包含 API，所以工作台没办法直接调用它。在这个模式下，每次需要 AI 时，工作台会准备好一段提示词：
把它粘贴给 ChatGPT，再把回复粘贴回来。提示词里包含你的声音档案和相关的脚本内容。

查热词永远是用这种方式，不管上面选的是哪种 AI，因为它需要一个当时就能联网搜索的 AI。那段提示词里放的是你的视频标题，不是脚本正文。

![复制粘贴对话框](docs/images/chatgpt-bridge-zh.png)

### 本地模型

先打开 Ollama 或 LM Studio 并下载好模型，再到「设置」里选择**本地模型**。工作台会列出已经安装的模型。只接受这台电脑上的地址。
请检查本地模型的上下文长度设置：提示词比较长（经常超过一万个字符），默认值太小的话会被截断。

### MCP 服务

`run_mcp.py` 是一个通过标准输入输出通信的 MCP（Model Context Protocol）服务。接上以后，由 AI 应用负责写，
工作台负责提供你的声音档案、以前的脚本、检查规则和保存。通过 MCP 保存的脚本会出现在工作台里。

提供的工具：`get_script_brief`、`save_script`、`update_script`、`check_script`、`list_scripts`、`get_script`、
`get_voice_profile`、`list_library`、`get_voice_learning_brief`、`save_voice_profile`、
`get_topic_ideas_brief`、`save_topic_ideas`、`export_jianying_kit`、`list_trends`、`add_trend_keywords`。

下面的配置方法来自各家截至 2026 年 11 月的官方文档，维护者都还没有实际验证过。
路径请用绝对路径；路径里没有空格可以避免引号问题。

**Codex（用 ChatGPT 订阅登录）**：在 `~/.codex/config.toml` 里加上：

```toml
[mcp_servers.learn_my_voice]
command = "python3"
args = ["/absolute/path/to/Learn-My-Voice/run_mcp.py"]
```

「设置」页面里会显示填好真实路径的这段配置。

**ChatGPT 网页版**：ChatGPT 可以添加自定义 MCP 服务，但服务必须能通过 HTTP 访问，或者通过 OpenAI 的 Secure MCP Tunnel 连接；
它不能自己启动本地程序。Tunnel 客户端可以启动本服务：

```bash
tunnel-client init --sample sample_mcp_stdio_local --profile learn-my-voice \
  --tunnel-id <你的 tunnel id> \
  --mcp-command "python3 /absolute/path/to/Learn-My-Voice/run_mcp.py"
tunnel-client run --profile learn-my-voice
```

按照 OpenAI 的说明，Tunnel 本身需要在 OpenAI Platform 上申请一个 tunnel id 和一个 API 密钥。
详见 [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server) 和
[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。

**Claude Desktop 和其他支持 stdio 的客户端**：

```json
{ "mcpServers": { "learn-my-voice": { "command": "python3", "args": ["/absolute/path/to/Learn-My-Voice/run_mcp.py"] } } }
```

然后在聊天里说，比如：*「用 Learn My Voice 帮我写一个 60 秒的脚本，讲我们周末去 Costco 补货。」*

## 功能

- **脚本编辑**：一句话一张卡片，可以填画面和花字，实时显示字数和大概要念多久，自动保存。
- **实时检查**（中英文提示）：一口气念不完的长句、你从来不说的词、营销号套话、翻译腔、可能违规的广告用语，以及全篇长度和目标时长是否匹配。这些是规则不是 AI，所以即时、每次结果一致。
- **学习声音**：从你自己的脚本里生成声音档案。每口气的字数范围是程序量出来的，不是模型猜的。商单脚本不会混进核心档案。旧档案会留作备份。
- **写初稿**：每次都会把你自己表现最好的脚本当作范例一起给 AI。每篇脚本可以设最多 8 个目标关键词：设了以后，会优先拿你讲过相关话题的脚本当范例，编辑页也会显示每个关键词有没有出现在口播里，或者 AI 写的备选标题、正文、话题里。AI 写初稿时一起写的备选标题、封面文字、正文和话题会显示在脚本下面，可以看、可以复制。
- **选题建议**：按互动量给以前的视频排名，建议接下来拍什么。每条建议必须引用你自己的视频；引用的标题如果对不上，会被去掉。正在跟踪的热词会作为第二个参考一起给 AI，建议里只能提到确实在跟踪的热词。
- **热点关键词**：把你看到的热词贴进来（后面可以带热度数字），工作台按「有多热、多新」和「你自己拍相关内容的表现」给它们排序，两边各占多少由你选。排序是算出来的，不是 AI。和你的声音档案冲突的词会标出来，不会出现在推荐里。也可以让能联网的聊天 AI 帮你查（通过复制粘贴对话框或 MCP）：查到的词必须带来源链接，并且会标明「未核实」。
- **提词器**：全屏，可调速度和字号。
- **导出**：剪映素材包、字幕 `.srt`、口播文稿、拍摄清单、花字清单。

![选题建议](docs/images/ideas-zh.png)

## 剪映 / CapCut

在脚本页点 **导出 → 剪映素材包**，会下载一个压缩包：

| 文件 | 用途 |
| :--- | :--- |
| `口播文稿.txt` | 一口气一行。粘贴到剪映的「文稿匹配」里，字幕会和你的实际录音对齐。 |
| `字幕.srt` | 可以导入的字幕文件。**时间是按语速估算的**，不是按录音，导入后需要微调。 |
| `花字.srt` | 花字，位置在对应那句话附近。 |
| `拍摄清单.txt` | 每一句对应要拍的画面。 |
| `使用说明.txt` | 导入步骤。 |

把界面切换成英文再导出，文件名和说明就是英文的。

![英文界面和导出菜单](docs/images/script-en.png)

目前还做不到的：直接生成一个剪映草稿（素材和文字已经排在时间线上），以及根据你的实际录音给字幕打时间。
剪映的草稿格式没有公开文档，新版本里也无法直接读取，主要的开源生成工具对 macOS 上生成的草稿也有限制说明，
所以这一步需要先在真实安装的剪映上测试。

## 数据和隐私

- 所有内容都保存在项目文件夹里的 `data/`：脚本、素材库、声音档案、设置。这个文件夹不会被 git 提交。
- API 密钥保存在 `data/config.json`，只有本人可读，也不会再传回浏览器。
- 网页服务只监听 `127.0.0.1`，拒绝 Host 不对的请求，也拒绝其他网站发来的写入请求。
- 使用 AI 时，会把你的声音档案、想法和部分以前的脚本发给你选择的那个 AI。用本地模型的话，内容不会离开这台电脑。
- 播放、点赞、收藏、评论都是手动填写的，不会从任何平台抓取数据。
- 热词要么是你自己贴进来的，要么是你自己去问的、能联网的 AI 查到的；工作台不会为了热词去访问任何网站。查热词的提示词里有你的视频标题、一句人设描述（学过声音才有）、你填的方向和已经在跟踪的热词，不包含脚本正文。

## 项目结构

| 路径 | 内容 |
| :--- | :--- |
| `studio/server.py`、`studio/web/` | 本地网页服务和界面 |
| `studio/guardrails.py`、`studio/lexicon.json`、`studio/checks.py` | 规则检查和中英文提示 |
| `studio/pipeline.py`、`studio/llm.py` | 生成提示词、校验回复、对接模型 |
| `studio/corpus.py` | 读取脚本、统计、表现排名 |
| `studio/trends.py` | 读取粘贴的热词、结合你自己的数据打分、检查关键词有没有用上 |
| `studio/exports.py` | 文本导出、`.srt`、剪映素材包 |
| `studio/mcp_server.py`、`run_mcp.py` | MCP 服务 |
| `prompts/` | 提示词原文。程序直接读取其中的 02、04、06、07，修改这些文件就会改变行为。 |
| `SYSTEM_DESIGN.md` | 最初的、更大的设计方案，其中一部分还没有实现。 |

提示词是按一个具体场景写的：一位中国博主，和说英语的丈夫一起拍日常和夫妻 Vlog。要给别人用，请修改 `prompts/`。

## 开发

```bash
python3 -m unittest discover -t . -s tests        # 全部测试，无依赖
python3 -m studio.guardrails tests/fixtures/worked_example_script.json --dna schemas/voice_dna.example.json
```

## 已知的限制

- 广告违规词表只是为这个项目收集的起点，没有对照平台或法规的最新要求核实过，不构成法律意见。
- 语速没法从文字里学出来，在你自己填写之前用的是默认值。
- 有数据的视频太少时，选题建议不太可靠，页面上会有提示。
- 热词的分数只是参考，不是测量结果。权重和「每 14 天减半」都是凭经验定的，还没有用真实结果校准过；热度数字填的是什么就是什么。判断一条视频和热词相不相关，靠的是有没有相同的字词，所以换了说法讲同一个话题的视频可能对不上。
- AI 查到的热词可不可靠，取决于它引用的那个网页。工作台只要求有网页链接，并且会去掉来源日期超过 30 天的词；它不会打开网页，所以没法确认网页上是不是真的那么写。这些词会标着「未核实」，除非你自己把同一个词贴进来。
- 商单 Brief 处理、二次审稿、从你的改稿中学习、视频转写，这些在 `prompts/` 和 `SYSTEM_DESIGN.md` 里有设计，但还没有实现。

## 许可

MIT，见 [LICENSE](LICENSE)。
