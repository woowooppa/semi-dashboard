#!/usr/bin/env python3
"""
반도체·AI 대시보드 일일 데이터 수집기.

수집 대상
  1) 주가  : Stooq CSV (API 키 불필요) — 삼성전자, SK하이닉스, 마이크론, SOXX
  2) 매크로: FRED API (무료 키 필요) — 반도체 PPI 계열
  3) 뉴스  : RSS — Reuters Tech, 전자신문, 디일렉

설계 원칙
  - 한 소스가 죽어도 전체가 죽지 않는다 (소스별 예외 격리)
  - 실패는 조용히 넘어가지 않고 errors 배열에 남긴다
  - 기존 data.json을 읽어, 이번에 못 받은 값은 직전 값을 stale 표시로 보존한다
"""

import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "data.json")
MANUAL_PATH = os.path.join(ROOT, "data", "manual.json")
UA = "Mozilla/5.0 (compatible; semi-dashboard/1.0)"
TIMEOUT = 25

# ---------------------------------------------------------------- 수집 대상 정의

TICKERS = {
    "005930.KS": {"stooq": "005930.kr", "label": "삼성전자",   "unit": "KRW"},
    "000660.KS": {"stooq": "000660.kr", "label": "SK하이닉스", "unit": "KRW"},
    "MU":        {"stooq": "mu.us",     "label": "마이크론",   "unit": "USD"},
    "SOXX":      {"stooq": "soxx.us",   "label": "SOXX",       "unit": "USD"},
}

FRED_SERIES = {
    "PCU3344133344131": "반도체·관련소자 제조 PPI — IC 패키지",
    "PCU334413334413":  "반도체·관련소자 제조 PPI",
}

FEEDS = [
    ("Reuters Technology", "https://www.reuters.com/arc/outboundfeeds/technology/?outputType=xml"),
    ("전자신문",           "https://rss.etnews.com/Section901.xml"),
    ("디일렉",             "https://www.thelec.kr/rss/S1N1.xml"),
]

# 뉴스는 전량이 아니라 관심 키워드가 걸린 것만 남긴다
KEYWORDS = [
    "반도체", "메모리", "디램", "DRAM", "낸드", "NAND", "HBM", "파운드리",
    "삼성전자", "하이닉스", "마이크론", "엔비디아", "TSMC", "AI", "인공지능",
    "semiconductor", "memory", "chip", "Nvidia", "Micron", "foundry", "wafer",
]
MAX_NEWS = 40

# 시계열 보존 길이
HISTORY_DAYS = 260    # 주가: 약 1년치 영업일
FRED_MONTHS = 61      # FRED: 약 5년치 월별 (YoY 계산에 13개 필요)


# ---------------------------------------------------------------- 공통 유틸

def fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_previous():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------- 1) 주가

def get_prices(errors):
    """Stooq 일별 CSV에서 최근 2영업일을 읽어 종가와 등락률을 만든다."""
    out = {}
    for key, meta in TICKERS.items():
        try:
            url = "https://stooq.com/q/d/l/?s={}&i=d".format(meta["stooq"])
            raw = fetch(url).decode("utf-8", "replace")
            rows = list(csv.DictReader(io.StringIO(raw)))
            rows = [r for r in rows if r.get("Close") not in (None, "", "N/A")]
            if not rows:
                raise ValueError("빈 응답 — 티커 확인 필요")

            last = rows[-1]
            close = float(last["Close"])
            prev = float(rows[-2]["Close"]) if len(rows) > 1 else close
            chg = ((close - prev) / prev * 100) if prev else 0.0

            # 최근 HISTORY_DAYS 영업일만 보존 (파일 크기 관리)
            hist = []
            for r in rows[-HISTORY_DAYS:]:
                try:
                    hist.append({"d": r["Date"], "c": round(float(r["Close"]), 2)})
                except Exception:
                    continue

            out[key] = {
                "label": meta["label"],
                "unit": meta["unit"],
                "close": round(close, 2),
                "prev_close": round(prev, 2),
                "chg_pct": round(chg, 2),
                "date": last.get("Date"),
                "source": "Stooq",
                "history": hist,
            }
        except Exception as e:
            errors.append("price:{} — {}".format(key, e))
    return out


# ---------------------------------------------------------------- 2) FRED

def get_fred(errors):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        errors.append("fred — FRED_API_KEY 미설정, 건너뜀")
        return {}

    out = {}
    for sid, desc in FRED_SERIES.items():
        try:
            url = (
                "https://api.stlouisfed.org/fred/series/observations"
                "?series_id={}&api_key={}&file_type=json"
                "&sort_order=desc&limit={limit}"
            ).format(sid, key, limit=FRED_MONTHS)
            data = json.loads(fetch(url).decode("utf-8"))
            obs = [o for o in data.get("observations", []) if o.get("value") not in (".", "", None)]
            if not obs:
                raise ValueError("유효 관측치 없음")

            latest = obs[0]
            value = float(latest["value"])
            yoy = None
            if len(obs) >= 13:
                try:
                    base = float(obs[12]["value"])
                    if base:
                        yoy = round((value - base) / base * 100, 2)
                except Exception:
                    pass

            hist = [{"d": o["date"], "c": float(o["value"])} for o in reversed(obs)]

            out[sid] = {
                "desc": desc,
                "value": value,
                "date": latest["date"],
                "yoy_pct": yoy,
                "source": "FRED / BLS",
                "history": hist,
            }
        except Exception as e:
            errors.append("fred:{} — {}".format(sid, e))
    return out


# ---------------------------------------------------------------- 3) 뉴스

def strip_tags(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_feed(name, url):
    raw = fetch(url)
    root = ET.fromstring(raw)
    items = []

    # RSS 2.0
    for it in root.iter("item"):
        items.append({
            "title": strip_tags((it.findtext("title") or "")),
            "link": (it.findtext("link") or "").strip(),
            "published": (it.findtext("pubDate") or "").strip(),
            "source": name,
        })

    # Atom
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for it in root.iter(ns + "entry"):
            link_el = it.find(ns + "link")
            items.append({
                "title": strip_tags(it.findtext(ns + "title") or ""),
                "link": (link_el.get("href") if link_el is not None else "") or "",
                "published": (it.findtext(ns + "updated") or "").strip(),
                "source": name,
            })
    return items


def get_news(errors):
    collected = []
    for name, url in FEEDS:
        try:
            collected.extend(parse_feed(name, url))
        except Exception as e:
            errors.append("news:{} — {}".format(name, e))

    # 키워드 필터 + 중복 제거
    seen, filtered = set(), []
    for it in collected:
        if not it["title"] or not it["link"]:
            continue
        blob = it["title"].lower()
        if not any(k.lower() in blob for k in KEYWORDS):
            continue
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        filtered.append(it)

    return filtered[:MAX_NEWS]


# ---------------------------------------------------------------- 4) 수기 로그

def get_manual(errors, prices):
    """manual.json을 읽어 시계열로 정리하고 파생지표를 계산한다.

    파생지표
      fwd_over_ttm : 선행 P/E ÷ 후행 P/E — '약속의 크기'. 낮을수록 이익 성장 기대가 큼
      rel_ttm      : SOXX 후행 ÷ 나스닥 후행 — 섹터 프리미엄(후행)
      rel_fwd      : SOXX 선행 ÷ 나스닥 선행 — 섹터 프리미엄(선행)
      implied_eps  : SOXX 종가 ÷ 후행 P/E — 지수 내재 EPS. 실현 이익의 성장 속도를 본다
    비율은 반드시 같은 entry(같은 시점) 안에서만 만든다.
    """
    try:
        with open(MANUAL_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        errors.append("manual — manual.json 없음, 건너뜀")
        return {}
    except Exception as e:
        errors.append("manual — 파싱 실패: {}".format(e))
        return {}

    # SOXX 종가를 날짜로 찾기 위한 색인
    soxx = {}
    for r in ((prices.get("SOXX") or {}).get("history") or []):
        soxx[r["d"]] = r["c"]

    def near_close(d):
        """해당 일자 이하의 가장 가까운 종가(휴장일 대응)."""
        cands = [k for k in soxx if k <= d]
        return soxx[max(cands)] if cands else None

    rows = []
    for e in sorted(raw.get("entries", []), key=lambda x: x.get("date", "")):
        d = e.get("date")
        if not d:
            continue
        st, sf = e.get("soxx_pe_trailing"), e.get("soxx_pe_forward")
        nt, nf = e.get("ndx_pe_trailing"), e.get("ndx_pe_forward")

        row = dict(e)
        row["fwd_over_ttm"] = round(sf / st, 3) if (st and sf) else None
        row["rel_ttm"] = round(st / nt, 2) if (st and nt) else None
        row["rel_fwd"] = round(sf / nf, 2) if (sf and nf) else None

        px = near_close(d)
        row["soxx_close"] = px
        row["implied_eps"] = round(px / st, 2) if (px and st) else None
        rows.append(row)

    return {"entries": rows, "fields": raw.get("_readme", [])}


# ---------------------------------------------------------------- 병합 · 저장

def carry_over(new, prev, section):
    """이번에 못 받은 항목은 직전 값을 stale로 표시해 보존한다."""
    old = (prev or {}).get(section) or {}
    for k, v in old.items():
        if k not in new:
            v = dict(v)
            v["stale"] = True
            new[k] = v
    return new


def main():
    errors = []
    prev = load_previous()

    prices = carry_over(get_prices(errors), prev, "prices")
    fred = carry_over(get_fred(errors), prev, "fred")
    news = get_news(errors) or (prev.get("news") or [])
    manual = get_manual(errors, prices)

    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices": prices,
        "fred": fred,
        "news": news,
        "manual": manual,
        "errors": errors,
        "notes": {
            "manual_fields": [
                "밸류에이션·메모리 가격은 data/manual.json에 수기로 누적한다",
                "선행 P/E는 애널리스트 컨센서스(유료)라 자동 수집이 구조적으로 불가",
                "3사 Bit Growth·ASP — 각 사 IR, 분기 1회 수기",
            ]
        },
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("생성: {}".format(OUT_PATH))
    print("  주가 {}건 / FRED {}건 / 뉴스 {}건 / 수기 {}건".format(
        len(prices), len(fred), len(news), len(manual.get("entries", []))))
    if errors:
        print("  경고 {}건:".format(len(errors)))
        for e in errors:
            print("   -", e)

    # 전부 실패한 경우에만 실패 처리 — 부분 실패는 통과시킨다
    if not prices and not fred and not news:
        print("모든 소스 실패", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
