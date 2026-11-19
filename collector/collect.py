"""
보험 상품 비교공시 수집 로봇
  - 손해보험협회(kpub.knia.or.kr)  : 장기보장성 비교공시 (JSON 형태)
  - 생명보험협회(pub.insure.or.kr) : 보장성 상품비교 상세공시 (HTML 표)
수집 결과를 Supabase 표(ins_*)에 저장합니다.

실행에 필요한 환경변수 (GitHub Secrets에 등록):
  SUPABASE_URL          https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role 키 (절대 웹페이지에 넣지 말 것)
선택:
  LINES                 수집할 보험종류. 예) "암"  또는 "암,치아"  (기본: 암)
  DRY_RUN=1             Supabase에 쓰지 않고 수집 결과만 출력
"""
import datetime
import os
import re
import sys
import time

import chompjs
import requests
from bs4 import BeautifulSoup

TODAY = datetime.date.today().isoformat()
UA = {"User-Agent": "Mozilla/5.0 (insurance-dashboard collector; contact via GitHub)"}

# 보험종류 -> (손보 tptyCode, 생보 prodGroup)   None = 해당 협회에 없음
LINES = {
    "암":        ("PB22", "024400010004"),
    "질병":      ("PB15", "024400010003"),
    "어린이":    ("PB24", "024400010007"),
    "간병·치매": ("PB16", "024400010010"),
    "운전자":    ("PB13", None),
    "치아":      ("PB25", "024400010009"),
    "종신":      (None,   "024400010001"),
    "정기":      (None,   "024400010002"),
    "CI":        (None,   "024400010005"),
    "상해":      ("PB14", "024400010006"),
}

KNIA_COMPANIES = ["N01", "N02", "N03", "N05", "N09", "N10", "N11", "N16", "N17", "N51", "N52", "N66", "N71"]


# ---------------------------------------------------------------- 공통 도우미
def num(v):
    """'8,400 원' -> 8400, '150.9 %' -> 150.9, '-' 또는 '%' -> None"""
    if v is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(v))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def won(text):
    """지급액 원문에서 첫 금액을 원 단위로: '1,000만원(1년미만 50%)' -> 10000000, '1억원' -> 100000000"""
    if not text:
        return None
    t = str(text).replace(",", "").replace(" ", "")
    m = re.search(r"(\d+(?:\.\d+)?)억(?:(\d+)천)?(?:(\d+)백)?(?:(\d+))?만?원", t)
    if m:
        v = float(m.group(1)) * 1e8
        if m.group(2): v += int(m.group(2)) * 1e7
        if m.group(3): v += int(m.group(3)) * 1e6
        if m.group(4): v += int(m.group(4)) * 1e4
        return int(v)
    m = re.search(r"(\d+(?:\.\d+)?)(천만|백만|십만|만|천)?원", t)
    if not m:
        return None
    mult = {"천만": 1e7, "백만": 1e6, "십만": 1e5, "만": 1e4, "천": 1e3, None: 1}[m.group(2)]
    return int(float(m.group(1)) * mult)


def refund_type(name, note=""):
    s = f"{name} {note}"
    if re.search(r"미지급|무해지|무해약", s):
        return "무해지"
    if re.search(r"저해지|저지급|일부지급|50‰|50%", s):
        return "저해지"
    return "표준형"


def underwriting(name):
    return "간편심사" if re.search(r"간편|\d{3}간편|3\.\d\.\d", name) else "일반심사"


# ---------------------------------------------------------------- 손보협회
def fetch_knia(tpty):
    params = [("tabType", "1"), ("tptyCode", tpty)] + [("pCode", c) for c in KNIA_COMPANIES] \
             + [("channel", c) for c in "ABCDE"]
    r = requests.get("https://kpub.knia.or.kr/popup/disclosureList.do", params=params, timeout=90,
                     headers={**UA, "Referer": "https://kpub.knia.or.kr/"})
    r.raise_for_status()
    # 응답 문자열 안에 날것의 줄바꿈/탭이 섞여 있어 그대로는 파싱 불가 -> 이스케이프
    text = r.text.replace("\r", "").replace("\n", "\\n").replace("\t", " ")
    return chompjs.parse_js_object(text, json_params={"strict": False})["list"]


def parse_knia(rows, line):
    prods = {}
    for r in rows:
        key = (r["TP_CODE"], r.get("TP_NEW_CHANNEL") or "")
        if key not in prods:
            pm, pf = num(r.get("INS_PAY_INDEX_M")), num(r.get("INS_PAY_INDEX_W"))
            # 일부 회사는 가격지수를 1.0x 비율로 공시 -> %로 환산
            pm = round(pm * 100, 1) if pm is not None and pm < 5 else pm
            pf = round(pf * 100, 1) if pf is not None and pf < 5 else pf
            name = r["TP_NAME"].strip()
            prods[key] = {
                "insurer": (r["P_CODE"], r["P_CODE_NM"], "손보"),
                "product": {
                    "source": "knia", "source_code": r["TP_CODE"], "insurer_code": r["P_CODE"], "line": line,
                    "name": name, "channel": r.get("TP_NEW_CHANNEL") or "",
                    "renewable": r.get("REFRESH_YN") == "Y", "refund_type": refund_type(name),
                    "underwriting": underwriting(name),
                    "std_condition": str(r.get("TP_ETC") or "").split("\n")[0].lstrip("ㆍ▶※-0. ").strip()[:200],
                    "summary_url": r.get("TP_URL") or None,
                },
                "premium": [("M", num(r.get("TP_M_BILL")), pm), ("F", num(r.get("TP_W_BILL")), pf)],
                "index": {
                    "cover_idx_diag": num(r.get("CANCER01")), "cover_idx_hosp": num(r.get("CANCER02")),
                    "load_idx": num(r.get("ADD_INSRNC_IDX")), "cost_idx": num(r.get("CNTRCT_CNCLS_IDX")),
                    "rate_guaranteed": num(r.get("TP_RATE1")),
                },
                "coverage": [],
            }
        prods[key]["coverage"].append({
            "part": "담보", "name": (r.get("TP_PAY_NAME") or "").strip(),
            "pay_reason": (r.get("TP_PAY_REASON") or "").strip() or None,
            "pay_amount_text": (r.get("TP_PAY") or "").strip() or None,
            "pay_amount_won": won(r.get("TP_PAY")),
        })
    return list(prods.values())


# ---------------------------------------------------------------- 생보협회
def fetch_klia(group):
    data = {"pageIndex": "1", "search_columnArea": "all", "search_prodGroup": group,
            "pageUnit": "1000", "search_order_sort": "asc"}
    r = requests.post("https://pub.insure.or.kr/compareDis/prodCompare/assurance/listNew.do", data=data,
                      timeout=120, headers={**UA, "Referer": "https://pub.insure.or.kr/"})
    r.raise_for_status()
    return r.text


def _txt(td):
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()


def _benefit(td):
    t = _txt(td)
    name, _, reason = t.partition("*지급사유")
    return name.strip(), reason.lstrip(" :").strip() or None


def _renewal(td):
    m = re.search(r"fn_fileDown\('(\d+)',\s*'(\d+)'\)", str(td))
    if m:
        return f"klia-file:{m.group(1)}:{m.group(2)}"
    t = _txt(td)
    return None if t in ("", "-") else t


def parse_klia(html, line):
    soup = BeautifulSoup(html, "lxml")
    table = max(soup.find_all("table"), key=lambda t: len(t.find_all("tr")))
    prods, cur, main_amt = [], None, None
    for tr in table.find("tbody").find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) > 20:                      # 상품 첫 줄
            inp = tds[0].find("input")
            code = inp["value"] if inp else _txt(tds[2])
            lab = tds[0].find("label", id=re.compile(r"^l_memberCd_"))
            member = _txt(lab) if lab else code[:3]
            company = _txt(tds[1])
            name = _txt(tds[2])
            refund_note = _txt(tds[19])
            channel = "·".join(x.replace("채널", "") for x in tds[23].get_text("\n", strip=True).split("\n") if x)
            sale = _txt(tds[24])
            m = re.search(r"fn_fileDown\('(\d+)',\s*'(\d+)'\)", str(tds[25]))
            pname, preason = _benefit(tds[4])
            main_amt = _txt(tds[5])
            cur = {
                "insurer": (member, company, "생보"),
                "product": {
                    "source": "klia", "source_code": code, "insurer_code": member, "line": line,
                    "name": name, "channel": channel, "renewable": _txt(tds[20]) != "비갱신형",
                    "refund_type": refund_type(name, refund_note), "underwriting": underwriting(name),
                    "sale_date": sale if re.match(r"\d{4}-\d{2}-\d{2}", sale) else None,
                    "std_condition": "주계약 기준(협회 표준조건)",
                    "summary_url": f"klia-file:{m.group(1)}:{m.group(2)}" if m else None,
                },
                "premium": [("M", num(_txt(tds[6])), num(_txt(tds[11]))), ("F", num(_txt(tds[7])), num(_txt(tds[12])))],
                "index": {
                    # 생보는 보장범위지수가 없는 상품을 0 %로 표기 -> 값 없음으로 저장
                    "cover_idx_diag": num(_txt(tds[13])) or None, "cover_idx_hosp": num(_txt(tds[14])) or None,
                    "load_idx": num(_txt(tds[15])), "cost_idx": num(_txt(tds[17])),
                    "rate_guaranteed": num(_txt(tds[8])) or num(_txt(tds[9])),
                    "refund_note": refund_note or None, "renewal_cycle": _txt(tds[20]) or None,
                    "expected_renewal_premium": _renewal(tds[21]),
                },
                "coverage": [{"part": _txt(tds[3]), "name": pname, "pay_reason": preason,
                              "pay_amount_text": main_amt, "pay_amount_won": won(main_amt),
                              "premium_m": num(_txt(tds[6])), "premium_f": num(_txt(tds[7]))}],
            }
            prods.append(cur)
        elif cur is not None and len(tds) >= 2:   # 같은 상품의 다음 급부/특약 줄
            part = _txt(tds[0])
            bname, breason = _benefit(tds[1])
            row = {"part": part, "name": bname, "pay_reason": breason}
            if len(tds) >= 5:
                amt = _txt(tds[2])
                row.update(pay_amount_text=amt, pay_amount_won=won(amt),
                           premium_m=num(_txt(tds[3])), premium_f=num(_txt(tds[4])))
            elif part == "주계약":
                # 공시에는 주계약 가입금액만 있음. 딸린 급부의 실제 지급액은 다를 수 있어 숫자로 넣지 않음
                row.update(pay_amount_text=f"주계약 가입금액 {main_amt} 기준(급부별 지급비율은 상품요약서 확인)",
                           pay_amount_won=None)
            cur["coverage"].append(row)
    return prods


# ---------------------------------------------------------------- Supabase 저장
class DB:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].strip().rstrip("/") + "/rest/v1/"
        key = os.environ["SUPABASE_SERVICE_KEY"].strip()
        if not key.isascii() or " " in key or not (key.startswith("eyJ") or key.startswith("sb_secret_")):
            raise RuntimeError("SUPABASE_SERVICE_KEY 형식이 이상함: 'eyJ'로 시작하는 service_role 키만 "
                               "공백·다른 글자 없이 다시 등록하세요 (키 값은 로그에 출력하지 않음)")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def upsert(self, table, rows, on_conflict, returning=False):
        if not rows:
            return []
        h = {**self.h, "Prefer": "resolution=merge-duplicates," + ("return=representation" if returning else "return=minimal")}
        out = []
        for i in range(0, len(rows), 500):
            r = requests.post(self.url + table, params={"on_conflict": on_conflict}, json=rows[i:i + 500], headers=h, timeout=60)
            if r.status_code >= 300:
                raise RuntimeError(f"{table}: {r.status_code} {r.text[:300]}")
            if returning:
                out += r.json()
        return out

    def insert(self, table, rows):
        for i in range(0, len(rows), 500):
            r = requests.post(self.url + table, json=rows[i:i + 500], headers={**self.h, "Prefer": "return=minimal"}, timeout=60)
            if r.status_code >= 300:
                raise RuntimeError(f"{table}: {r.status_code} {r.text[:300]}")

    def delete(self, table, **filters):
        r = requests.delete(self.url + table, params={k: v for k, v in filters.items()}, headers=self.h, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"{table} delete: {r.status_code} {r.text[:300]}")


def save(db, prods):
    insurers = {p["insurer"][0]: {"code": p["insurer"][0], "name": p["insurer"][1], "sector": p["insurer"][2]} for p in prods}
    db.upsert("ins_insurer", list(insurers.values()), "code")

    rows = []
    for p in prods:
        rows.append({**p["product"], "last_seen": TODAY})
    saved = db.upsert("ins_product", rows, "source,source_code,channel", returning=True)
    ids = {(s["source"], s["source_code"], s["channel"]): s["id"] for s in saved}

    prem, idx, cov = [], [], []
    for p in prods:
        pr = p["product"]
        pid = ids[(pr["source"], pr["source_code"], pr["channel"])]
        for sex, premium, pidx in p["premium"]:
            prem.append({"product_id": pid, "collected_on": TODAY, "sex": sex, "age": 40,
                         "premium": int(premium) if premium is not None else None, "price_index": pidx})
        idx.append({"product_id": pid, "collected_on": TODAY, **{k: p["index"].get(k) for k in (
            "cover_idx_diag", "cover_idx_hosp", "load_idx", "cost_idx", "rate_guaranteed",
            "refund_note", "renewal_cycle", "expected_renewal_premium")}})
        for c in p["coverage"]:
            cov.append({"product_id": pid, "collected_on": TODAY, "part": c.get("part"), "name": c["name"] or "-",
                        "pay_reason": c.get("pay_reason"), "pay_amount_text": c.get("pay_amount_text"),
                        "pay_amount_won": c.get("pay_amount_won"),
                        "premium_m": int(c["premium_m"]) if c.get("premium_m") is not None else None,
                        "premium_f": int(c["premium_f"]) if c.get("premium_f") is not None else None})
    db.upsert("ins_premium", prem, "product_id,collected_on,sex,age")
    db.upsert("ins_index", idx, "product_id,collected_on")
    # 같은 날 다시 돌려도 담보가 두 번 쌓이지 않게 오늘 것을 지우고 새로 넣음
    for pid in sorted(set(c["product_id"] for c in cov)):
        db.delete("ins_coverage", product_id=f"eq.{pid}", collected_on=f"eq.{TODAY}")
    db.insert("ins_coverage", cov)
    return len(prem) + len(idx) + len(cov)


# ---------------------------------------------------------------- 실행
def main():
    wanted = [x.strip() for x in os.environ.get("LINES", "암").split(",") if x.strip()]
    dry = os.environ.get("DRY_RUN") == "1"
    db = None if dry else DB()
    failed = False
    for line in wanted:
        knia_code, klia_code = LINES[line]
        for source, code in (("knia", knia_code), ("klia", klia_code)):
            if not code:
                continue
            try:
                prods = parse_knia(fetch_knia(code), line) if source == "knia" else parse_klia(fetch_klia(code), line)
                if not prods:
                    raise RuntimeError("0건 수집 - 협회 화면 구조가 바뀌었을 수 있음")
                n = 0 if dry else save(db, prods)
                ncov = sum(len(p["coverage"]) for p in prods)
                msg = f"{source} {line}: 상품 {len(prods)}개, 담보 {ncov}행"
                print(msg)
                if dry:
                    print(prods[0])
                else:
                    db.insert("ins_collect_log", [{"source": source, "line": line, "products": len(prods),
                                                   "rows_loaded": n, "status": "ok", "message": msg}])
            except Exception as e:  # 한 곳이 실패해도 나머지는 계속
                failed = True
                print(f"[실패] {source} {line}: {e}", file=sys.stderr)
                if db:
                    try:
                        db.insert("ins_collect_log", [{"source": source, "line": line, "status": "error", "message": str(e)[:500]}])
                    except Exception:
                        pass
            time.sleep(3)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
