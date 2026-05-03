-- Migration 0002 (BIZ-77): messages 테이블에 channel 컬럼 추가.
--
-- 인사이트 → source 메시지 역추적(F: Insight Source Linkage) 시 Admin UI에
-- "어느 채널(텔레그램/웹훅/콘솔/cron 등)의 발화인지"를 함께 노출하기 위한 기반.
-- 컬럼은 NULL 허용 — 기존 메시지는 채널 정보가 없고, 신규 producer(채널 핸들러)
-- 가 점진적으로 채워 넣는다. NULL은 "unknown/legacy"로 해석한다.
--
-- E(BIZ-76 Cron/Recipe 메시지 코퍼스 분리)가 같은 컬럼에 "cron"/"recipe"를 태깅해
-- 인사이트 가중치 다운에 활용할 예정이므로, 컬럼 도입 자체는 F에서 한 번에 처리한다.

ALTER TABLE messages ADD COLUMN channel TEXT;
