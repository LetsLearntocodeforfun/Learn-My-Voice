# RedNote De-AI-Taste & Compliance Replacement Lexicon (`去AI味 + 违禁词人话词典`)

This reference sheet powers two deterministic + LLM guardrails in the pipeline:
1. **Stage 4 & 5A ("De-AI-ifier")**: Eliminates translationese (`翻译腔`), stiff written Mandarin (`书面语`), and generic influencer clichés (`营销号黑话`).
2. **Stage 3 & 5A (RedNote Compliance Filter)**: Prevents `蒲公英` review rejections (`报备驳回`) and algorithmic shadowbanning (`限流`) on sponsored and product-share posts.

---

## 1. Translationese & Bookish Mandarin -> Spoken Breath (`去翻译腔 / 去书面语`)

When English ideas or brand briefs are translated by standard LLMs, these stiff patterns appear constantly. Always replace with spoken Mandarin rhythm:

| AI / Translationese (`典型AI味/书面语`) | Natural Spoken Mandarin (`真人日常口语替换`) | Why It Sounds Fake (EN Note) |
| :--- | :--- | :--- |
| `它完美地解决了我的问题` | `一下就省事儿多了` / `刚好治好了我的纠结` | Direct calque of "It perfectly solved my problem" |
| `这真的改变了我的游戏规则` | `用了之后真的回不去啦` | Literal translation of "game-changer" |
| `它不仅…而且还…` | `除了…之外嘛，最戳我的是…` | Essay conjunction; nobody speaks in `不仅…而且` |
| `给我留下了深刻的印象` | `我当时一试还蛮惊喜的` | Textbook composition phrase |
| `进行一个涂抹/搭配的动作` | `直接往脸上抹` / `顺手搭一下` | Bureaucratic/livestream filler (`进行一个X`) |
| `十分轻盈 / 非常滋润` | `上脸水水的` / `润润的不拔干` | `十分/非常` + abstract adj = zero tactile feel |
| `总而言之 / 综上所述` | `反正整体用下来呢` / `好啦` | Essay summary transition |
| `无论是在…还是在…场景下` | `平时上班或者周末出门都能穿` | Written parallel clause structure |
| `让我们一起来看看` | `你们看` / *(just cut straight to shot)* | TV host cliché |
| `开启元气满满的一天` | `先煮杯咖啡醒醒神` | Overused generic vlog cliché |

---

## 2. Generic RedNote Hype Clichés (`小红书营销号黑话 — Default Blacklist`)

Unless explicitly found in her real organic videos (almost never for authentic lifestyle creators), block all of the following:

- **Forced Greetings**: `家人们谁懂啊`、`宝子们`、`集美们`、`仙女们`、`老婆们`
- **Empty Superlatives**: `绝绝子`、`yyds`、`天花板`、`封神了`、`直接封神`、`王炸组合`、`神仙单品`
- **Pushy Sales Hype**: `按头安利`、`闭眼入`、`不买后悔一辈子`、`听我的准没错`、`人手一件`、`冲就完了`
- **Vague Buzzwords**: `满满的高级感`、`氛围感拉满`、`狠狠拿捏`、`心智`、`赋能`、`抓手`、`组合拳`

---

## 3. RedNote Ad-Law & Traffic-Safety Replacements (`小红书违禁词 -> 安全口语替换`)

RedNote's automated review (`机审`) flags absolute superlatives (`极限词`) and medical/unverified cosmetic claims, quietly throttling impressions (`限流`) or rejecting `蒲公英` sponsored drafts.

### A. Absolute Superlatives (`广告法极限词`)
| Banned / High-Risk Word | Safe Personal-Voice Alternative (`安全人话`) | English Meaning |
| :--- | :--- | :--- |
| `最好用 / 最舒服 / 第一` | `我今年用得最勤的一罐` *(personal habit, or)* `我最近天天都在抓它` | Avoid `最` on objective claims; frame as personal frequency (`穿/用得最多`) |
| `100% 有效 / 绝对不脱妆` | `带妆跑一天下来基本没怎么斑驳` | Realistic lived observation beats `100%` |
| `全网最低价 / 秒杀大牌` | `这个价位能做成这样真的蛮良心的` | Avoids price-war & competitor disparagement flags |
| `国家级 / 顶级 / 极致` | `做工细节很扎实` / `质感很在线` | Removes banned `极限词` |

### B. Beauty & Skincare (`美妆护肤敏感词 — 非特字号化妆品`)
| Banned / Risky Claim | Safe RedNote Colloquial Replacement | Compliance Reason |
| :--- | :--- | :--- |
| `美白 / 快速祛斑` *(unless 持证美白特妆)* | `提亮肤色`、`去黄气`、`素颜看着很干净透亮` | Whitening requires special NMPA registration |
| `消炎 / 杀菌 / 治疗痘痘 / 根治泛红` | `换季脸颊不闹脾气`、`把不稳定的状态安抚下来`、`退红维稳` | Medical/pharmaceutical terms (`消炎/治疗`) strictly banned on cosmetics |
| `抗衰老 / 逆龄 / 再生细胞` | `抗初老`、`脸摸着紧致饱满一点`、`状态显年轻` | Cellular regeneration & medical anti-aging trigger ad review |
| `立竿见影 / 一夜回春` | `连着厚涂两三天，早上洗脸手感就不一样啦` | Realistic timeframe + tactile feel passes audit & builds viewer trust |

### C. Hard-Commerce & Off-Platform Triggers (`导流与硬广限流词`)
- **Avoid in non-component organic posts**: `加微`、`VX`、`私信发链接`、`点击下方购买`、`下单立减`、`库存不多赶紧抢`.
- **Natural RedNote Engagement Closing**: `你们平时秋冬还有什么离不开的宝藏也可以在评论区跟我聊聊哈～`
