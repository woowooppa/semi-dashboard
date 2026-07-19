# 반도체·AI 투자 대시보드

승자를 맞히는 대신 병목을 소유한다는 전제를, **지속적으로 반증하기 위한** 대시보드.

## 구조

```
index.html                  대시보드 본체 (단일 파일)
data/data.json              자동 수집 결과 — Actions가 매일 생성
scripts/fetch_data.py       수집기
.github/workflows/update.yml 매일 06:00 KST 실행
docs/                       항목별 정리 문서
```

## 자동 / 수동 구분

**자동 (매일 06:00 KST)**
- 주가 — 삼성전자, SK하이닉스, 마이크론, SOXX (Stooq, 키 불필요)
- 반도체 PPI — FRED (무료 API 키 필요)
- 뉴스 — Reuters Tech, 전자신문, 디일렉 RSS (키워드 필터 적용)

**수동 — 자동화하지 않는다**
| 항목 | 주기 | 이유 |
|---|---|---|
| DRAM 현물가·계약가·DXI | 주 1회 | TrendForce 유료·재배포 불가 |
| SOXX 후행 P/E | 주 1회 | iShares 공식 페이지 직접 확인 |
| SOXX 선행 P/E | 분기 | 무료 신뢰 소스 없음 |
| 3사 Bit Growth·ASP | 분기 | 각 사 IR 원문 정독 필요 |

유료 데이터는 자동 스크래핑하지 않는다. 링크와 요약만 싣는다.

## 설정

1. FRED 무료 키 발급 → https://fredaccount.stlouisfed.org/apikeys
2. 저장소 `Settings → Secrets and variables → Actions → New repository secret`
   - Name: `FRED_API_KEY`
3. `Actions` 탭 → `데이터 갱신` → `Run workflow`로 첫 실행

## 로컬 실행

```bash
export FRED_API_KEY=발급받은키
python scripts/fetch_data.py
```

`index.html`은 `file://`로 열면 `data/data.json` fetch가 CORS로 막힌다.
VS Code의 **Live Server** 확장으로 열거나 아래처럼 띄운다.

```bash
python -m http.server 8000
# http://localhost:8000
```

## 갱신 실패 시

- 대시보드 상단 표시등이 붉게 바뀌고 "경고 N건"이 뜬다
- 브라우저 콘솔에 실패한 소스가 찍힌다
- 한 소스가 죽어도 나머지는 갱신된다. 못 받은 값은 직전 값을 `직전값` 표시로 유지한다

## 주의

수치는 정보 정리이며 투자 자문이 아니다. 판단 전 각 사 IR·에프앤가이드·iShares 등 원출처에서 재확인할 것.
