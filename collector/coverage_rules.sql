-- 3일차 · 담보 공통 분류(담보그룹)
-- 회사마다 다른 담보명을 같은 그룹으로 묶는 규칙. 우선순위 숫자가 작은 규칙부터 적용.
-- 규칙만 고치면 기존 담보도 다시 분류할 수 있음 (맨 아래 update 문)

create table ins_coverage_rule (
  id bigint primary key generated always as identity,
  priority int not null,
  pattern text not null,          -- 정규식 (담보명에서 '(…제외)' 부분을 지운 뒤 검사)
  coverage_group text not null,
  note text
);

insert into ins_coverage_rule (priority, pattern, coverage_group, note) values
  (10,  '납입면제',                                         '납입면제',     '보험료 납입면제 조건'),
  (15,  '만기|중도급여|건강관리자금|축하금',                  '만기·축하금',  '저축성 성격 급부'),
  (20,  '뇌|심근|심장|심혈관|허혈|협심',                     '뇌·심장',      '암보험에 붙은 2대·3대 질병 특약'),
  (30,  '암.*사망|사망.*암',                                 '암사망',       null),
  (35,  '사망',                                              '사망',         '상해·질병 사망 (필수가입 담보 포함)'),
  (40,  '장해',                                              '후유장해',     null),
  (50,  'CT|MRI|PET|검사|촬영',                              '검사비',       null),
  (60,  '수술',                                              '수술',         '다빈치·로봇 수술 포함'),
  (70,  '항암|방사선|약물|표적|양성자|중입자|카티|호르몬|주요치료|치료비|치료보장|치료자금|치료보험금|치료급여|통증완화', '항암·주요치료', null),
  (80,  '입원|통원|요양병원|일당',                           '입원·통원',    null),
  (85,  '암종별',                                            '암진단',       '30종 암종별 통합암진단: 상품당 1개 설계로 볼 것'),
  (90,  '통합전이암|전이암진단|원격전이|계속받는|재진단|4기', '전이·재진단',  '(전이포함)은 일반 암진단이라 제외'),
  (100, '유사암|소액암|소액\s*진단|갑상선|기타피부암|제자리암|경계성|대장점막내암', '유사암·소액암 진단', null),
  (105, '양성종양|용종|골절|신부전|소액질병',               '기타 질병·상해', '암 이외 진단 특약'),
  (110, '암',                                                '암진단',       '일반암·통합암·고액암·특정암 진단');

-- 담보명 정리: '(유사암제외)', '[암(기타피부암·갑상선암 제외)]' 같은 제외 문구를 지움
create or replace function ins_clean_coverage_name(n text) returns text
language sql immutable as $$
  select regexp_replace(coalesce(n, ''), '[(\[][^()\[\]]*제외[^()\[\]]*[)\]]', '', 'g')
$$;

-- 담보명 -> 담보그룹
create or replace function ins_classify_coverage(n text) returns text
language sql stable as $$
  select coalesce(
    (select r.coverage_group from ins_coverage_rule r
      where ins_clean_coverage_name(n) ~ r.pattern
      order by r.priority limit 1),
    '기타')
$$;

-- 새로 들어오는 담보는 자동 분류
create or replace function ins_coverage_set_group() returns trigger
language plpgsql as $$
begin
  new.coverage_group := ins_classify_coverage(new.name);
  return new;
end $$;

create trigger ins_coverage_group_trg
  before insert or update of name on ins_coverage
  for each row execute function ins_coverage_set_group();

-- 이미 들어온 담보 분류
update ins_coverage set coverage_group = ins_classify_coverage(name) where true;

-- 규칙 표도 읽기 전용 공개
alter table ins_coverage_rule enable row level security;
create policy "read" on ins_coverage_rule for select to anon using (true);
