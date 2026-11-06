# 设计文档

这份文档说明这个项目为什么这样设计、每一步怎么算。读完它，应该能在不看聊天记录的情况下接着改代码。

写于 2026-11-06，当时是 RTA 秋季赛季（`pvp_rta_ss21`，Fall 2026）。

---

## 1. 目标和边界

**目标**：第七史诗（Epic Seven）RTA 选人阶段的参谋。玩家把双方的预 Ban、选人填进页面，页面根据高段位真实对局数据给出：

- 我方下一手选谁（估计胜率 + 原因）
- 对方下一手可能选谁
- 10 人选完后，Ban 对方哪个角色；以及对方最可能 Ban 我方哪个

**不做的事**：不操作游戏、不自动点击、不代打。这违反游戏用户协议，对对手也不公平。工具只读取公开的对局记录，选人由玩家自己完成。

---

## 2. 数据来源

### 2.1 对局记录接口（官方，无公开文档）

Smilegate 的官方对局记录网站 `https://epic7.onstove.com/en/gg` 背后调用的接口：

```
POST https://e7api.onstove.com/gameApi/getBattleList?nick_no={玩家编号}&world_code={服务器}&lang=en
Accept: application/json
```

- 不需要登录，请求体为空。
- 返回该玩家**当前赛季最近 100 场** RTA 对局。
- 响应外层是 `{"code": 0, "message": "SUCCESS", "value": {"result_body": {"battle_list": [...]}}}`。响应可能带 UTF-8 BOM，用 `utf-8-sig` 解码。
- `world_code` 取值：`world_global`、`world_asia`、`world_kor`、`world_eu`、`world_jpn`。高段位跨服匹配，对手可能在别的服。

接口来源：参考了 GitHub 项目 [CloudeaSoft/e7-rta-history](https://github.com/CloudeaSoft/e7-rta-history) 的 `scripts/archive.mjs`，再在官方网站的网络请求里确认。官方网站还调用 `gameApi/getUserInfo`、`getUserInfoSeason`、`getSeasonList`、`getRecommendList`，本项目没用到。

### 2.2 每场对局里用到的字段

| 字段 | 含义 |
|---|---|
| `nicknameno` / `worldCode` / `grade_code` | 被查询的玩家（"我方"）的编号、服务器、段位 |
| `matchPlayerNicknameno` / `enemy_world_code` / `enemy_grade_code` | 对手的编号、服务器、段位。爬虫靠这个找下一个玩家 |
| `iswin` | 1 = 我方赢 |
| `battle_day` | 对局时间，双方记录里一致，用来去重 |
| `season_code` | 赛季，如 `pvp_rta_ss21` |
| `my_deck.hero_list[]` / `enemy_deck.hero_list[]` | 双方 5 个角色：`hero_code`、`first_pick`（1 = 这队先手）、`ban`（1 = 这个角色在选完后被对方 Ban 掉） |
| `my_deck.preban_list` / `enemy_deck.preban_list` | 双方各 2 个预 Ban |
| `teamBettleInfo` / `teamBettleInfoenemy` | **字符串**，形如 `"my_team":[{...}]`，要在外面补一对 `{}` 才是合法 JSON。里面有每个角色的 `pick_order`（队内第几手）、神器、套装、伤害等 |

段位从低到高：`bronze, silver, gold, master, challenger, champion, warlord, emperor, legend`。

没用到但有的字段：`energyGauge`（开场行动条）、`opening_rule_title`（开场规则）、`turn`、`battle_time`、`myscore_info`（积分变化），以及 `teamBettleInfo` 里的神器和套装。

### 2.3 角色名称表

```
GET https://static-pubcomm.onstove.com/gameRecord/epic7/epic7_hero.json
```

按语言分组：`{"en": [...], "zh-CN": [...], "zh-TW": [...], ...}`，每项 `{code, grade, name, job_cd, attribute_cd}`。页面用 `zh-CN` 名字。

**坑**：旧地址 `static.smilegatemegaport.com/gameRecord/epic7/epic7_hero.json` 还能访问，但停在 370 个角色，缺最近的新角色（黎洁特 `c2186`、里安娜路西艾拉 `c2185` 等）。要用上面的 `static-pubcomm.onstove.com`，2026-11 时有 393 个。

职业 `job_cd`：knight 骑士、warrior 战士、assassin 刺客、ranger 射手、mage 魔导士、manauser 精灵师。
属性 `attribute_cd`：fire、ice、wind、light、dark。

---

## 3. RTA 选人规则（模型的前提）

- 双方各预 Ban 2 个，预 Ban 的角色双方都不能选。双方可以 Ban 同一个。
- 选人顺序：先手方 1 → 后手方 2 → 先手 2 → 后手 2 → 先手 2 → 后手 1，共 10 人。
- 角色池共用，一方选了的另一方不能再选。
- 10 人选完后，双方各 Ban 掉对方 1 个，剩 4 打 4。

页面的轮次计算（`seq()` / `nextTurn()`）就是按这个顺序。

---

## 4. 抓取（`crawl.py`）

### 4.1 策略：顺着对手往高段位走

从一个种子玩家开始，抓他的 100 场，把对手加进队列，再抓对手，如此循环。

- 队列是**按段位排序的优先队列**，段位越高越先抓，所以很快就爬到传说、皇帝段。
- 只把段位 ≥ `--min-grade`（默认 champion）的对手加进队列。
- 默认种子是 `199070445:world_asia`，来自上面那个公开项目，只是起点，可以用 `--seed` 换掉。

为什么不用排行榜做种子：没找到公开的排行榜接口。顺着对手走效果也很好，400 个玩家里有 270 个皇帝段、93 个传说段。

### 4.2 去重

同一场对局会出现在双方的记录里。去重键是 `min(双方编号)-max(双方编号)-battle_day`。实测抓第二个玩家时，100 场里通常有 1 到 20 场是重复的，说明这个键有效。

### 4.3 礼貌和续抓

- 每次调用间隔 `--delay`，默认 1.2 秒。400 个玩家大约 15 分钟。
- 失败最多重试 3 次，间隔递增。
- `players` 表记录每个玩家的抓取时间。`--refetch-hours`（默认 12）内抓过的不再请求，但会用库里已有的对局继续扩展队列，所以中断后可以接着跑。

### 4.4 存储（`e7.db`，SQLite）

```sql
players(nick, world, grade, fetched_at, status)          -- 主键 (nick, world)
battles(key PRIMARY KEY, season, day,
        a_nick, a_world, a_grade, a_win, a_draft,          -- a = 被查询的一方
        b_nick, b_world, b_grade, b_draft)
```

`a_draft` / `b_draft` 是 JSON：

```json
{"picks": ["c6005", "c2007", "c2046", "c5112", "c5033"],   // 按 pick_order 排序
 "banned": "c5033",                                         // 被对方选完后 Ban 掉的，可能为 null
 "prebans": ["c2185", "c2181"],
 "first": false}                                            // 这队是否先手
```

`e7.db` 不进 git：里面有大量其他玩家的编号，而且能重新生成。

---

## 5. 统计（`stats.py`）

### 5.1 取哪些对局

- 只取最新赛季（按 `MAX(day)` 判断），可用 `--season` 指定。
- 双方段位都 ≥ `--min-grade`（默认 champion）。
- 双方都正好 5 个选人。

### 5.2 每个角色的计数

对每场对局的每一方：

| 字段 | 定义 |
|---|---|
| `picked` | 被选进 5 人的次数 |
| `postban` | 选上后被对方 Ban 掉的次数 |
| `played` | `picked - postban`，即真正上场的次数 |
| `wins` | 上场时赢的次数 |
| `preban` | 出现在任一方预 Ban 里的**对局数**。双方都 Ban 只算 1 次 |
| `fp` / `fpWins` | 作为先手方第一手的次数和胜场 |

由于角色池共用，`picked / 总场数` 就是出场率。

### 5.3 搭配和克制（只算上场的 4 人）

- **搭配 `pairs`**：同一队上场的每两个角色 `(h1, h2)`，记场数和胜场。无方向。
- **克制 `vs`**：我方上场的 h 对敌方上场的 e，记场数和 h 的胜场。只存 `h < e`（按编号字符串比较）的一半，另一半用 `场数 - 胜场` 推出。

### 5.4 输出

- 只保留 `played ≥ 30` 或 `preban ≥ 30` 的角色（`--min-games`）。
- 只保留场数 ≥ 15 的搭配和克制组合（`--min-pair`）。
- 角色按 `picked + preban` 排序，`pairs` / `vs` 用角色数组下标表示 `[i, j, 场数, 胜场]`，以缩小体积。
- 结果写进 `stats.json`，同时替换 `template.html` 里的 `/*__STATS__*/null` 生成 `draft.html`。生成的页面是单文件，离线可用。

---

## 6. 推荐模型（`template.html` 里的脚本）

### 6.1 公式

在对数几率（logit）上做加法：

```
base[h] = (wins[h] + K·0.5) / (played[h] + K)          K = 150，往 50% 收缩
lb[h]   = logit(base[h])

lift(h, x) = logit( (胜场 + KP·base[h]) / (场数 + KP) ) − lb[h]     KP = 60，往 h 自己的胜率收缩
             （x 是队友时查 pairs，x 是敌人时查 vs；没有记录则为 0）

score(h | 队友 M, 敌人 E) = lb[h] + Σ_{m∈M} lift_pair(h,m) + Σ_{e∈E} lift_vs(h,e)
估计胜率 = sigmoid(score)
```

原因标签（"配 X +2.0%"、"克 X +3.5%"、"怕 X −1.8%"）把每一项换成百分点：`sigmoid(lb[h] + lift) − base[h]`，取绝对值最大的 2 到 3 个显示。

### 6.2 三个用途

| 面板 | 怎么算 |
|---|---|
| 我方下一手推荐 | 所有没被选、没被预 Ban、`played ≥ 100`（`MIN_REC`）的角色，按 `score` 排序。开了「只推荐我有的」就只在我的角色里选 |
| 对方可能选 | 同样的候选，按 `log(出场率 + 0.005) + 4·score` 排序。猜对手要以热门程度为主，"最强"不等于"最可能被选" |
| 选完后 Ban 谁 | 对方每个角色 e：`score(e | 对方其他人, 我方全部)`，越高威胁越大，排第一的建议 Ban。我方角色同理，算出最可能被对方 Ban 的 |

### 6.3 参数为什么这样定

最初用 `K = 40`、`KP = 25`、最低 30 场，推荐榜前几名被 39 场、61.5% 胜率的冷门角色占据（例如自由骑士雅洛薇），显然是小样本噪声。调成 `K = 150`、`KP = 60`、最低 100 场后，推荐的都是几百到几千场样本的角色。`played < 300`（`FEW`）的会标「样本少」。

### 6.4 已知局限

- **重复计算**：角色自身胜率里已经包含了"平均队友"和"平均对手"的影响，再加上每一项修正会有重复，估计胜率偏乐观（经常 55% 到 65%）。适合用来**排序**，不要当成真实概率。
- **各项独立相加**：没有考虑三个角色以上的组合效应。
- **看不到配装、练度和操作**：这些在 RTA 里影响很大。
- **样本偏差**：只有被爬到的玩家的最近 100 场，偏向皇帝、传说段，不是全服数据。
- **选完后 Ban 的模型很简单**：没有用"实际被 Ban 后胜率变化"这种更直接的数据。

### 6.5 可以改进的方向

1. 用逻辑回归或梯度提升树，以"双方 4 人上场阵容 → 胜负"为样本训练，替代简单相加，能解决重复计算问题。
2. 选完后 Ban：直接统计"对方 5 人里 Ban 掉 X 后我方胜率"，比威胁分更准。
3. 时间衰减：平衡改动后，让最近的对局权重更高。
4. 利用 `first_pick` 和 `pick_order`，分开统计先手、后手和每一手的最佳选择。
5. 利用神器和套装字段，给推荐角色附上最常见的配装。
6. 个人分析：用自己的编号抓自己的对局，统计自己每个角色的胜率、最常输给什么阵容。
7. 定时更新：用 GitHub Actions 每天跑 `crawl.py` 和 `stats.py`，再用 GitHub Pages 发布 `draft.html`。

---

## 7. 页面（`template.html`）

- 单个 HTML 文件，不依赖框架。只从 Google Fonts 加载字体，没网时会退回系统字体。
- 支持深色和浅色模式，手机宽度（约 380px）下也能用。
- 状态保存在浏览器 `localStorage`，键名为 `e7draft.draft`（当前对局）、`e7draft.roster`（我有的角色，存角色编号）、`e7draft.rosterMode`。读写都包了 try/catch，存不了也能正常用。
- 第一次打开会根据数据生成一局示例：最常被预 Ban 的角色当预 Ban，最常见的先手第一手当我方第一手，最热门的两个当对方前两手，并显示「示例」提示条。
- 填完一个格子后，会自动跳到下一个该填的格子：先填完 4 个预 Ban，再按选人顺序填。
- 页面最初发布在 claude.ai 的 Artifact 上，那里的限制是：外部脚本只能来自几个 CDN、不能发网络请求，所以数据必须内嵌进页面。

---

## 8. 已做过的验证

- **和 epic7rtastats.com 对比**：两边口径接近。黎洁特预 Ban 率这边 90.6%，那边 87.1%；天秤之主这边 65.6%，那边 55.7%。这边偏高是因为样本更偏高段位。
- **选人流程**：在无界面 Edge 里模拟点击，从清空、4 个预 Ban、按 1-2-2-2-2-1 选满 10 人，到标记 Ban 人，每一步的轮次提示都正确。
- **排版**：用无界面 Edge 截图检查了 1280px 和 380px 宽度，没有横向溢出。

---

## 9. 规则和注意事项

- 接口没有公开文档，属于非官方用法，官方改版可能失效。使用前看一下游戏和网站的使用条款。
- 抓取保持低频（默认 1.2 秒一次），不要并发。
- 原始数据库里有其他玩家的编号，不要公开发布。公开的只有汇总统计。
- 只做数据分析和选人参考，不做任何游戏内自动化。
