-- 보험 상품 비교 대시보드 · 창고(표) 6개
-- 손보협회(knia) + 생보협회(klia) 공시를 같은 구조로 저장

-- ① 회사
create table ins_insurer (
  code text primary key,                 -- 협회 회사코드 (예: N09)
  name text not null,                    -- 현대해상
  sector text not null check (sector in ('손보','생보'))
);

-- ② 상품
create table ins_product (
  id bigint primary key generated always as identity,
  source text not null check (source in ('knia','klia')),   -- 어느 협회 공시인지
  source_code text not null,             -- 협회 상품코드
  insurer_code text not null references ins_insurer(code),
  line text not null,                    -- 보험종류: 암, 종신, 정기, 어린이 ...
  name text not null,
  channel text not null default '',      -- 대면, TM, CM, 방카 ...
  renewable boolean,                     -- 갱신형 여부
  refund_type text,                      -- 표준형 / 저해지 / 무해지
  underwriting text,                     -- 일반심사 / 간편심사
  sale_date date,
  std_condition text,                    -- 공시 기준조건 (40세, 100세만기 20년납 ...)
  summary_url text,
  first_seen date not null default current_date,
  last_seen date not null default current_date,
  unique (source, source_code, channel)
);

-- ③ 보험료 (수집할 때마다 날짜별로 쌓음)
create table ins_premium (
  id bigint primary key generated always as identity,
  product_id bigint not null references ins_product(id) on delete cascade,
  collected_on date not null default current_date,
  sex text not null check (sex in ('M','F')),
  age int not null default 40,
  premium bigint,                        -- 월보험료(원)
  price_index numeric,                   -- 보험가격지수 (100 = 업계 평균)
  unique (product_id, collected_on, sex, age)
);

-- ④ 담보 상세
create table ins_coverage (
  id bigint primary key generated always as identity,
  product_id bigint not null references ins_product(id) on delete cascade,
  collected_on date not null default current_date,
  part text,                             -- 주계약 / 특약 / 담보
  name text not null,                    -- 협회에 적힌 담보명 그대로
  coverage_group text,                   -- 공통 분류: 암진단, 유사암, 암수술, 항암치료 ...
  pay_reason text,                       -- 지급사유
  pay_amount_text text,                  -- 지급액 원문 (예: 1,000만원(1년미만 50%))
  pay_amount_won bigint,                 -- 숫자로 바꾼 지급액(원)
  premium_m bigint,                      -- 담보별 보험료(생보 특약)
  premium_f bigint
);

-- ⑤ 기타 지수 · 환급 · 갱신
create table ins_index (
  id bigint primary key generated always as identity,
  product_id bigint not null references ins_product(id) on delete cascade,
  collected_on date not null default current_date,
  cover_idx_diag numeric,                -- 보장범위지수(암진단)
  cover_idx_hosp numeric,                -- 보장범위지수(암입원)
  load_idx numeric,                      -- 부가보험료지수
  cost_idx numeric,                      -- 계약체결비용지수
  rate_guaranteed numeric,               -- 공시이율/예정이율(%)
  refund_note text,                      -- 해약환급금 정보(생보)
  renewal_cycle text,                    -- 갱신주기
  expected_renewal_premium text,         -- 예상갱신보험료
  unique (product_id, collected_on)
);

-- ⑥ 수집 기록
create table ins_collect_log (
  id bigint primary key generated always as identity,
  run_at timestamptz not null default now(),
  source text not null,
  line text,
  products int,
  rows_loaded int,
  status text,
  message text
);

-- 조회 속도용 색인
create index on ins_product (line, insurer_code);
create index on ins_premium (product_id, collected_on);
create index on ins_coverage (product_id);
create index on ins_coverage (coverage_group);

-- 보안: 읽기만 허용 (쓰기는 수집 로봇의 비밀 키로만)
alter table ins_insurer     enable row level security;
alter table ins_product     enable row level security;
alter table ins_premium     enable row level security;
alter table ins_coverage    enable row level security;
alter table ins_index       enable row level security;
alter table ins_collect_log enable row level security;
create policy "read" on ins_insurer     for select to anon using (true);
create policy "read" on ins_product     for select to anon using (true);
create policy "read" on ins_premium     for select to anon using (true);
create policy "read" on ins_coverage    for select to anon using (true);
create policy "read" on ins_index       for select to anon using (true);
create policy "read" on ins_collect_log for select to anon using (true);

-- 손보 13개사 기본 등록
insert into ins_insurer (code, name, sector) values
  ('N01','메리츠화재','손보'),('N02','한화손보','손보'),('N03','롯데손보','손보'),
  ('N05','흥국화재','손보'),('N09','현대해상','손보'),('N10','KB손보','손보'),
  ('N11','DB손보','손보'),('N16','AXA손보','손보'),('N17','하나손보','손보'),
  ('N51','AIG손보','손보'),('N52','라이나손보','손보'),('N66','신한EZ손보','손보'),
  ('N71','농협손보','손보');
