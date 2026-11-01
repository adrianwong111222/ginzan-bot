# 銀山溫泉空房 bot

每個鐘查 13 間旅館官網（2 大人、1 晚、入住 2027/1/14–1/18）。

- 有新空房：即刻 send Telegram。
- 每日香港時間朝早 9 點後：send 一次「仲喺度行緊」。
- 連續 3 次查唔到某間：send 警告（可能個網改咗）。
- 1/18 之後自動停。

## 查邊度

| 旅館 | 官網訂房系統 |
|---|---|
| 銀山荘、古勢起屋別館、本館古勢起屋、瀧見館、能登屋、昭和館、永澤平八、いとうや、やなだ屋、藤屋、松本 | nj-yoyaku.net（月曆 ○△×） |
| 古山閣、蔵の場（古山閣新館） | yado-sagashi.net（每個房型月曆） |

旅館 list 來自 https://www.ginzanonsen.jp/yado/

## 設定（做一次）

### 1. 開 Telegram bot
1. Telegram 搵 `@BotFather`，send `/newbot`，跟住改名。
2. 佢會俾你一個 token（好似 `123456:ABC...`）。收好，唔好俾人睇。

### 2. 攞每個人嘅 chat id（分開 send，唔使開 group）
1. 你同另一個人，各自喺 Telegram 搵你個 bot，撳 **Start**（或者 send `/start`）。
   唔撳 Start 嘅人，bot 係 send 唔到俾佢嘅。
2. 用瀏覽器開（將 `<TOKEN>` 換做你個 token）：
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 搵每個 `"chat":{"id":123456789,...` 嘅數字。兩個人會有兩個唔同數字，睇 `"first_name"` 分邊個打邊個。
4. 如果乜都冇（`"result":[]`），叫佢哋再 send 一句嘢俾個 bot，然後 refresh。

### 3. 放上 GitHub
1. 喺 https://github.com/new 開一個 **Private** repo（例如 `ginzan-bot`），乜都唔好剔。
2. 喺呢個 folder 跑：
   ```bash
   git remote add origin https://github.com/<你個username>/ginzan-bot.git
   git push -u origin main
   ```
3. Repo → Settings → Secrets and variables → Actions → New repository secret，加兩個：
   - `TELEGRAM_BOT_TOKEN` = 你個 token
   - `TELEGRAM_CHAT_IDS` = 兩個 chat id，用逗號分開，例如 `123456789,987654321`

### 4. 試
1. Repo → Actions → ginzan-check → Run workflow。
2. `test_dates` 填 `2026-11-03`（一個而家有房嘅日子），撳 Run。
3. 兩個人都應該各自收到「有房」訊息。收到就搞掂。
4. 再 Run 一次，`test_dates` 留空：應該收到「仲喺度行緊」。

## 本機試

```bash
python check.py --dry-run
```

```bash
python check.py --dry-run --dates 2026-11-03
```
