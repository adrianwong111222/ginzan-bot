"""銀山溫泉空房檢查：13 間旅館官網訂房系統，2 大人 1 晚，入住 2027-01-14 至 2027-01-18。

有新空房就 send Telegram；每日 send 一次「仲喺度行緊」。
用法：
  python check.py                       正常跑
  python check.py --dry-run             唔 send，只 print
  python check.py --dates 2026-11-03    用其他日子測試（唔讀唔寫 state）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
DATES = [date(2027, 1, d) for d in range(14, 19)]  # 入住日，19 號退房
ADULTS = 2
HEARTBEAT_HOUR = 9  # 香港時間朝早 9 點後 send 每日報告
ERROR_ALERT_RUNS = 3  # 連續幾次出錯就即刻通知
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# 銀山温泉観光協会官網列出嘅旅館，佢哋官網嘅「予約」都係用 nj-yoyaku.net
NJ_HOTELS = [
    ("仙峡の宿 銀山荘", "ginzanso"),
    ("古勢起屋別館", "kosekiya"),
    ("本館古勢起屋", "kosekikan"),
    ("滝と蕎麦の宿 瀧見館", "takimikan"),
    ("能登屋旅館", "notoya"),
    ("味とまごころの宿 昭和館", "showakan"),
    ("旅館 永澤平八", "nagasawa"),
    ("旅籠 いとうや", "itouya"),
    ("御宿 やなだ屋", "yanadaya"),
    ("藤屋", "fujiya"),
    ("旅館 松本", "matsumoto"),
]
# 古山閣同蔵の場（古山閣新館）共用 yado-sagashi.net
KOZANKAKU_YID = "0046353751453"
YS_BASE = "https://www.yado-sagashi.net/yoyaku/plan/"
HOTEL_COUNT = len(NJ_HOTELS) + 2

WEEKDAY = "一二三四五六日"
SOLD_OUT_MARKS = {"×", "-", "－", "--", "", "　"}


def fetch(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - 網絡錯誤重試
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last}")


def months_of(dates):
    out = {}
    for d in dates:
        out.setdefault((d.year, d.month), []).append(d)
    return out


def check_nj(name, code, dates):
    """nj-yoyaku 嘅月曆：每格 ○/△/×，有 onclick 即係可以訂。"""
    slots = []
    for (y, m), ds in months_of(dates).items():
        url = f"https://www.nj-yoyaku.net/{code}/re_calendar.ashx?TDT={y}{m:02d}01&NZ={ADULTS}"
        h = fetch(url)
        head = re.search(r"<h5>(\d{4})年(\d{1,2})月</h5>", h)
        if not head or (int(head.group(1)), int(head.group(2))) != (y, m):
            raise RuntimeError(f"calendar header not found for {y}-{m}")
        cells = {int(day): (attrs, mark.strip())
                 for attrs, day, mark in re.findall(r"<td class='\w+'([^>]*)>(\d+)<br />([^<]*)</td>", h)}
        for d in ds:
            if d.day not in cells:
                raise RuntimeError(f"day {d} missing from calendar")
            attrs, mark = cells[d.day]
            if "onclick" in attrs or mark not in SOLD_OUT_MARKS:
                slots.append({
                    "hotel": name, "date": d.isoformat(), "room": "", "info": mark,
                    "url": f"https://www.nj-yoyaku.net/{code}/plan.aspx?TDT={d:%Y%m%d}&NZ={ADULTS}",
                })
    return slots


def kozankaku_rooms():
    """由古山閣計劃一覽攞每個 plan/房 嘅 p 值，同埋係本館定新館（蔵の場）。"""
    h = fetch(f"{YS_BASE}index2.jsp?beg&all&yid={KOZANKAKU_YID}")
    rooms = {}
    for mt in re.finditer(r"open2\('(\d+)',(\d+),'[^']*','(\d+)'", h):
        p = int(mt.group(2))
        text = re.sub(r"<[^>]+>", "|", h[max(0, mt.start() - 1500):mt.start()])
        found = re.findall(r"([^|()（）]+?)\s*[(（](\d+)名～(\d+)名[)）]", text)
        if not found:
            continue
        room, lo, hi = found[-1]
        if not (int(lo) <= ADULTS <= int(hi)):
            continue
        room = re.sub(r"\s+", " ", room).strip()
        hotel = "蔵の場（古山閣新館）" if ("新館" in room or "オーベルジュ" in room) else "古山閣"
        rooms[p] = (hotel, room)
    if not rooms:
        raise RuntimeError("no rooms found on plan list")
    return rooms


def check_kozankaku(dates):
    slots = []
    for p, (hotel, room) in kozankaku_rooms().items():
        for (y, m), ds in months_of(dates).items():
            url = (f"{YS_BASE}plcal.jsp?y={KOZANKAKU_YID}&p={p}&pe={ADULTS}"
                   f"&in={urllib.parse.quote(f'{ds[0]:%Y/%m/%d}')}&cmfrm=1")
            h = fetch(url)
            if f"{y}年{m}月" not in h:
                raise RuntimeError(f"plan {p}: calendar {y}-{m} not shown")
            cells = {}
            for c in re.findall(r'<div class="callink">(.*?)</div>\s*</td>', h, re.S):
                day = re.search(r'cal_p_day">(\d+)<', c)
                if day:
                    cells[int(day.group(1))] = c
            for d in ds:
                c = cells.get(d.day)
                if c is None:
                    raise RuntimeError(f"plan {p}: day {d} missing")
                bg = re.search(r'cal_bg">([^<]*)<', c)
                bg = bg.group(1).strip() if bg else ""
                price = re.search(r"<b>([^<]*)</b>", c)
                if "openpl(" in c or re.search(r"\d+\s*室", bg):
                    slots.append({
                        "hotel": hotel, "date": d.isoformat(), "room": room,
                        "info": f"{bg} {price.group(1) if price else ''}/人".strip(),
                        "url": (f"{YS_BASE}plcal.jsp?y={KOZANKAKU_YID}&p={p}&pe={ADULTS}"
                                f"&in={d:%Y/%m/%d}&cmfrm=1"),
                    })
            time.sleep(1)
    return slots


def check_all(dates):
    slots, errors = [], []
    for name, code in NJ_HOTELS:
        try:
            slots += check_nj(name, code, dates)
        except Exception as e:  # noqa: BLE001 - 一間壞咗唔好影響其他
            errors.append(f"{name}: {e}")
        time.sleep(1)
    try:
        slots += check_kozankaku(dates)
    except Exception as e:  # noqa: BLE001
        errors.append(f"古山閣/蔵の場: {e}")
    unique = {}
    for s in slots:  # 同一間同一日同一款房，唔同 plan 只報一次
        unique.setdefault(slot_key(s), s)
    return list(unique.values()), errors


def fmt_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def slot_key(s):
    return f"{s['hotel']}|{s['date']}|{s['room']}"


def alert_text(slots, new_keys):
    lines = [f"銀山溫泉有房！（{ADULTS} 大人，1 晚）", ""]
    for s in sorted(slots, key=lambda s: (s["date"], s["hotel"])):
        tag = "[新] " if slot_key(s) in new_keys else ""
        room = f" {s['room']}" if s["room"] else ""
        lines += [f"{tag}{s['hotel']} 入住 {fmt_date(s['date'])}{room} {s['info']}", s["url"], ""]
    lines.append("快啲去官網訂。")
    return "\n".join(lines)


def heartbeat_text(slots, errors, dates):
    span = f"{fmt_date(dates[0].isoformat())}–{fmt_date(dates[-1].isoformat())}" if dates else "-"
    lines = [
        "銀山溫泉 bot 仲喺度行緊。",
        f"每個鐘查 {HOTEL_COUNT} 間官網，入住 {span}，{ADULTS} 大人 1 晚。",
        f"而家有房：{len(slots)} 個" + ("（之前已經 send 過）" if slots else ""),
    ]
    if errors:
        lines += ["", "今次查唔到："] + [f"- {e}" for e in errors]
    return "\n".join(lines)


def send(text, dry_run):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if dry_run or not token or not chat_ids:
        print("----- (not sent) -----\n" + text + "\n----------------------")
        return
    for chat_id in chat_ids:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            print(f"sent to {chat_id}")
        except Exception as e:  # noqa: BLE001 - 一個 chat 失敗照 send 其他
            print(f"telegram send failed for {chat_id}: {e}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="唔 send Telegram")
    ap.add_argument("--dates", help="測試用入住日，逗號分隔，例如 2026-11-03,2026-11-04")
    args = ap.parse_args()

    now = datetime.now(HKT)
    if args.dates:
        dates = [date.fromisoformat(x.strip()) for x in args.dates.split(",")]
    else:
        dates = [d for d in DATES if d >= now.date()]
        if not dates:
            print("所有入住日已經過咗，唔再查。")
            return

    slots, errors = check_all(dates)
    print(f"{now:%Y-%m-%d %H:%M} HKT  slots={len(slots)}  errors={len(errors)}")
    for e in errors:
        print("ERROR", e, file=sys.stderr)

    if args.dates:  # 測試模式：有就 send，唔郁 state
        send(alert_text(slots, {slot_key(s) for s in slots}) if slots
             else f"測試：{args.dates} 冇房。", args.dry_run)
        return

    state = load_state()
    current = {slot_key(s) for s in slots}
    new_keys = current - set(state.get("seen", []))
    if new_keys:
        send(alert_text(slots, new_keys), args.dry_run)
    state["seen"] = sorted(current)

    state["error_runs"] = state.get("error_runs", 0) + 1 if errors else 0
    if state["error_runs"] == ERROR_ALERT_RUNS:
        send("銀山溫泉 bot：連續 3 次查唔到部分旅館，可能個網改咗：\n"
             + "\n".join(f"- {e}" for e in errors), args.dry_run)

    today = now.date().isoformat()
    if now.hour >= HEARTBEAT_HOUR and state.get("last_heartbeat") != today:
        send(heartbeat_text(slots, errors, dates), args.dry_run)
        state["last_heartbeat"] = today

    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
